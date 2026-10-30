"""Periodic and one-off background work owned by the application lifespan."""

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from config_engine.crypto import CredentialCipher
from fastapi import FastAPI
from sqlalchemy import select

from api.bootstrap.core import CoreServices
from api.bootstrap.runtime import cluster_bus_enabled
from api.db.models import McpServerEntityModel
from api.db.session import DatabaseSessionManager, current_tenant_org_id
from api.db.store import TurnTraceStore
from api.services import outbound_webhooks
from api.services.audit import RetentionPolicy, RetentionSweeper
from api.services.budgets import BudgetService
from api.services.cluster import LeaderElection
from api.services.interrupted_work import clear_stuck_processing, save_interrupted_turns
from api.services.mcp_engine import probe_and_fetch_tools
from api.services.mcp_runtime import REMOTE_TRANSPORTS, auth_headers, canonical_transport
from api.services.missions import MissionStore, claim_due
from api.services.provider_catalogue import REFRESH_SECONDS, provider_catalogue
from api.services.queued_messages import stranded_sessions
from api.services.settings import load_org_config
from api.services.subagent_runs import supervise_subagent_runs
from api.services.trigger_schedule import fire_due_schedules
from api.services.triggers import forget_old_deliveries
from api.services.turn_resume import resume_interrupted_turns
from api.services.turn_trace import (
    LEASE_HEARTBEAT_SECONDS,
    LEASE_TTL_SECONDS,
    PROCESS_INSTANCE_ID,
    active_turn_sessions,
    beat_turn_leases,
    recover_interrupted_turns,
    session_turn_activity,
)
from api.services.waits import (
    SessionWait,
    claim_due_waits,
    group_waits_by_session,
    wake_prompt_for,
)
from api.services.workspace_release import (
    delay_resolver,
    idle_candidates,
    release_idle_workspaces,
)
from api.services.workspace_snapshots import before_pause_hook
from api.turns.tasks import (
    _spawn_background_turn,
    launch_mission,
    resume_claimed_turn,
    start_queued_turns,
)
from specifications.interfaces.events import EventType, SessionEvent

logger = logging.getLogger(__name__)

SANDBOX_REAPER_SECONDS = 30
RETENTION_SWEEP_SECONDS = 3600
MCP_REFRESH_SECONDS = 900
MISSION_POLL_SECONDS = 30
WORKSPACE_RELEASE_SECONDS = 3600
WAITS_SWEEP_SECONDS = 5.0
DRAIN_POLL_SECONDS = 2.0
# Stranded messages wait at least a minute (STRANDED_AFTER_SECONDS) anyway.
QUEUED_SWEEP_SECONDS = 30.0
SPEND_SWEEP_SECONDS = 300
SCHEDULE_TRIGGER_SECONDS = 60
WEBHOOK_RETRY_SECONDS = 30


async def _run_periodically(
    label: str,
    interval_seconds: float,
    action: Callable[[], Awaitable[None]],
    *,
    run_immediately: bool = False,
    failure_level: int = logging.WARNING,
) -> None:
    """Run ``action`` forever; one failing round is logged and never ends the loop."""
    wait_first = not run_immediately
    while True:
        try:
            if wait_first:
                await asyncio.sleep(interval_seconds)
            wait_first = True
            await action()
        except asyncio.CancelledError:
            break
        except Exception as err:
            logger.log(failure_level, "%s error: %s", label, err)


async def _cancel(task: asyncio.Task[None] | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class BackgroundServices:
    """Starts every periodic task the control plane needs and stops them on shutdown."""

    def __init__(self, app: FastAPI, core: CoreServices) -> None:
        self._app = app
        self._core = core
        self._tasks: list[asyncio.Task[None]] = []
        self._admin_manager: DatabaseSessionManager | None = None

    def start(self) -> None:
        admin_manager: DatabaseSessionManager | None = None
        if self._core.db_manager is not None:
            # Governance retention, turn leases, missions, MCP refresh, and sandbox reaper
            # sweep every tenant, so they run on the schema-owner connection like migrations do.
            admin_manager = DatabaseSessionManager(
                os.getenv("ADMIN_DATABASE_URL") or self._core.database_url or ""
            )
            self._admin_manager = admin_manager
            self._start_retention(admin_manager)
            self._start_turn_recovery(admin_manager)
            self._start_missions(admin_manager)
            self._start_mcp_refresh(admin_manager)
            self._start_workspace_release(admin_manager)
            self._start_waits_sweeper(admin_manager)
            self._start_queued_messages_sweeper(admin_manager)
            self._start_spend_reservation_sweeper(admin_manager)
            self._start_automation(admin_manager)
        else:
            self._start_waits_sweeper(None)
            self._start_queued_messages_sweeper(None)
            self._start_spend_reservation_sweeper(None)
        self._start_sandbox_reaper(admin_manager)
        self._start_provider_catalogue()

    async def stop(self) -> None:
        for task in reversed(self._tasks):
            await _cancel(task)
        self._tasks.clear()
        if self._admin_manager is not None:
            await self._admin_manager.close()
            self._admin_manager = None

    async def wait_for_running_turns(self, seconds: float) -> int:
        """Give this replica's running turns up to ``seconds`` to finish; those still running.

        A turn the process is stopped under is interrupted and resumed with a recap; one that
        finishes first is not. Zero (the default) does not wait.
        """
        if seconds <= 0 or self._admin_manager is None:
            return 0
        store = TurnTraceStore(self._admin_manager)
        deadline = time.monotonic() + seconds
        remaining = await store.running_count(PROCESS_INSTANCE_ID)
        while remaining and time.monotonic() < deadline:
            await asyncio.sleep(DRAIN_POLL_SECONDS)
            remaining = await store.running_count(PROCESS_INSTANCE_ID)
        if remaining:
            logger.warning("Stopping with %d turn(s) still running", remaining)
        return remaining

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        self._tasks.append(asyncio.ensure_future(coroutine))

    def _start_provider_catalogue(self) -> None:
        """Keep the models' context windows current from the provider's own catalogue."""
        if os.getenv("ROCKET_PROVIDER_CATALOGUE", "1").strip().lower() in {"0", "false", "no"}:
            return

        async def _refresh() -> None:
            await provider_catalogue.refresh()

        self._spawn(
            _run_periodically(
                "Provider model catalogue", REFRESH_SECONDS, _refresh, run_immediately=True
            )
        )

    def _start_sandbox_reaper(self, admin_manager: DatabaseSessionManager | None) -> None:
        driver = self._core.sandbox_driver
        if not hasattr(driver, "reconcile_inactivity"):
            return
        if hasattr(driver, "before_idle_pause"):
            driver.before_idle_pause = before_pause_hook(
                driver, self._app.state.session_store, self._core.db_manager
            )

        async def _session_is_live(session_id: str) -> bool:
            return await self._app.state.session_store.get_session(session_id) is not None

        async def _reap() -> None:
            active_sessions = (
                await active_turn_sessions(admin_manager) if admin_manager is not None else None
            )
            session_idleness = (
                await session_turn_activity(admin_manager) if admin_manager is not None else None
            )
            await driver.reconcile_inactivity(
                is_live=_session_is_live,
                active_sessions=active_sessions,
                session_idleness=session_idleness,
            )
            if hasattr(driver, "reconcile_warm_pool"):
                await driver.reconcile_warm_pool()

        async def _nothing_to_release() -> None:
            return None

        async def _reap_loop() -> None:
            await _run_periodically("Sandbox background reaper", SANDBOX_REAPER_SECONDS, _reap)

        async def _reaper() -> None:
            cluster_dsn = self._core.cluster_dsn
            if cluster_bus_enabled() and cluster_dsn:
                await LeaderElection(cluster_dsn, "sandbox-reaper").run(
                    _reap_loop, _nothing_to_release
                )
            else:
                await _reap_loop()

        self._spawn(_reaper())

    def _start_retention(self, admin_manager: DatabaseSessionManager) -> None:
        sweeper = RetentionSweeper(
            admin_manager,
            policy_provider=lambda org_id: retention_policy_for(admin_manager, org_id),
        )

        async def _sweep() -> None:
            await sweeper.sweep()

        self._spawn(_run_periodically("Retention sweep", RETENTION_SWEEP_SECONDS, _sweep))

    def _start_turn_recovery(self, admin_manager: DatabaseSessionManager) -> None:
        """Continue turns the previous process left interrupted, and keep recovering in a cluster."""

        async def _settle_interrupted_turns() -> None:
            """Show what interrupted turns did, and free the sessions they left looking busy."""
            sessions = getattr(self._app.state, "session_store", None)
            if sessions is None:
                return
            transcripts = getattr(self._app.state, "transcript_store", None)
            await save_interrupted_turns(admin_manager, sessions, transcripts)
            await clear_stuck_processing(admin_manager, sessions)

        async def _resume_claimed_turns() -> None:
            await _settle_interrupted_turns()
            await resume_interrupted_turns(
                admin_manager,
                self._core.db_manager,
                lambda turn: resume_claimed_turn(self._app, turn),
            )

        async def _heartbeat_and_recover() -> None:
            # Replicas prove they are alive and recover turns whose replica went silent. A
            # single replica has no peers to recover, so the loop only runs in a cluster.
            await beat_turn_leases(admin_manager)
            await recover_interrupted_turns(admin_manager, stale_after_seconds=LEASE_TTL_SECONDS)
            await _resume_claimed_turns()

        async def _resume_after_restart() -> None:
            try:
                await _resume_claimed_turns()
            except Exception as err:
                logger.warning("Resuming interrupted turns after restart failed: %s", err)

        if cluster_bus_enabled():
            self._spawn(
                _run_periodically(
                    "Turn lease loop", LEASE_HEARTBEAT_SECONDS, _heartbeat_and_recover
                )
            )
        # Turns the previous process was running (marked interrupted by the recovery pass at
        # start-up) continue now that the orchestrator and stores exist.
        self._spawn(_resume_after_restart())

        # Background subagents: this replica's leases are renewed and runs whose replica
        # stopped (this process's predecessor included) are settled, cluster or not.
        async def _supervise_subagents() -> None:
            await supervise_subagent_runs(admin_manager)

        self._spawn(
            _run_periodically(
                "Subagent run supervision", LEASE_HEARTBEAT_SECONDS, _supervise_subagents
            )
        )

    def _start_missions(self, admin_manager: DatabaseSessionManager) -> None:
        mission_store = MissionStore(self._core.db_manager)
        self._app.state.mission_store = mission_store

        async def _fire_due_missions() -> None:
            for mission in await claim_due(admin_manager, mission_store):
                try:
                    session_id = await launch_mission(self._app, mission)
                    await mission_store.mark_run(mission.org_id, mission.id, session_id, "started")
                except Exception as err:
                    logger.exception("Mission %s could not start: %s", mission.id, err)
                    await mission_store.mark_run(mission.org_id, mission.id, None, "failed")

        async def _poll() -> None:
            await _run_periodically(
                "Mission scheduler", MISSION_POLL_SECONDS, _fire_due_missions, run_immediately=True
            )

        async def _nothing_to_release() -> None:
            return None

        async def _scheduler() -> None:
            cluster_dsn = self._core.cluster_dsn
            if cluster_bus_enabled() and cluster_dsn:
                # Claiming is atomic, so any replica could fire missions; electing one keeps
                # the polling load and the logs to a single replica.
                await LeaderElection(cluster_dsn, "mission-scheduler").run(
                    _poll, _nothing_to_release
                )
            else:
                await _poll()

        self._spawn(_scheduler())

    def _start_automation(self, admin_manager: DatabaseSessionManager) -> None:
        """Schedule triggers (each minute) and the retries of outbound webhook reports. Every
        replica runs both: a run and an attempt are each claimed by one of them."""
        app, db_mgr = self._app, self._core.db_manager

        async def _schedules() -> None:
            await fire_due_schedules(app, admin_manager)

        async def _retries() -> None:
            await outbound_webhooks.retry_due(admin_manager, db_mgr)

        async def _retention() -> None:
            await forget_old_deliveries(admin_manager)
            await outbound_webhooks.forget_finished(admin_manager)

        self._spawn(_run_periodically("Schedule triggers", SCHEDULE_TRIGGER_SECONDS, _schedules))
        self._spawn(_run_periodically("Outbound webhook retries", WEBHOOK_RETRY_SECONDS, _retries))
        self._spawn(_run_periodically("Automation retention", RETENTION_SWEEP_SECONDS, _retention))

    def _start_spend_reservation_sweeper(
        self, admin_manager: DatabaseSessionManager | None
    ) -> None:
        """Give back the spend a crashed call reserved and never settled. Every replica may
        sweep: a reservation is deleted once, by whoever deletes it first."""
        guard = getattr(self._core.orchestrator.gateway, "budget_guard", None)
        if not isinstance(guard, BudgetService):
            return

        async def _sweep() -> None:
            released = await guard.release_stale(admin_manager)
            if released:
                logger.warning("Released %d spend reservations whose call never settled", released)

        self._spawn(_run_periodically("Spend reservation sweeper", SPEND_SWEEP_SECONDS, _sweep))

    def _start_workspace_release(self, admin_manager: DatabaseSessionManager) -> None:
        """Delete the workspace volume of sessions idle past their delay (days, so hourly).
        Every replica may sweep: releasing is idempotent and skips a running sandbox."""
        core = self._core

        async def _release() -> None:
            await release_idle_workspaces(
                await idle_candidates(admin_manager),
                core.sandbox_driver,
                core.session_store,
                delay_resolver(core.db_manager),
            )

        self._spawn(_run_periodically("Workspace release", WORKSPACE_RELEASE_SECONDS, _release))

    def _start_mcp_refresh(self, admin_manager: DatabaseSessionManager) -> None:
        # Runs on the schema-owner connection: it must see every tenant's servers, which the
        # row-level-security role (no org context here) would not.
        cipher = CredentialCipher()

        async def _refresh() -> None:
            await _refresh_remote_mcp_servers(admin_manager, cipher)

        self._spawn(
            _run_periodically(
                "MCP refresh loop", MCP_REFRESH_SECONDS, _refresh, failure_level=logging.DEBUG
            )
        )

    def _start_queued_messages_sweeper(self, admin_manager: DatabaseSessionManager | None) -> None:
        """Start the turns for messages no turn took because their replica stopped.

        A session with a turn running anywhere is left alone: that turn starts them when it ends.
        """

        async def _sweep() -> None:
            locks = getattr(self._app.state, "session_write_locks", None)
            store = getattr(self._app.state, "session_store", None)
            if admin_manager is not None and store is not None:
                # Without this a session whose process died stays "busy" and queues for nothing.
                await clear_stuck_processing(admin_manager, store)
            for org_id, session_id in await stranded_sessions(admin_manager):
                record = await self._session_record(org_id, session_id)
                if record is None or record.is_processing:
                    continue
                if locks is not None and locks.is_locked(session_id):
                    continue
                await start_queued_turns(self._app, org_id, session_id)

        self._spawn(_run_periodically("Queued messages sweeper", QUEUED_SWEEP_SECONDS, _sweep))

    async def _session_record(self, org_id: str, session_id: str) -> Any:
        store = getattr(self._app.state, "session_store", None)
        if store is None:
            return None
        token = current_tenant_org_id.set(org_id)
        try:
            return await store.get_session(session_id)
        finally:
            current_tenant_org_id.reset(token)

    def _start_waits_sweeper(self, admin_manager: DatabaseSessionManager | None) -> None:
        async def _sweep_waits() -> None:
            wait_store = getattr(self._app.state, "wait_store", None)
            if not wait_store:
                return
            claimed = await claim_due_waits(admin_manager, wait_store)
            for session_id, waits in group_waits_by_session(claimed).items():
                try:
                    await self._wake_claimed_waits(waits, wait_store)
                except Exception as err:
                    logger.exception("Failed to wake session %s for its waits: %s", session_id, err)

        self._spawn(_run_periodically("Waits sweeper", WAITS_SWEEP_SECONDS, _sweep_waits))

    async def _wake_claimed_waits(self, waits: list[SessionWait], wait_store: Any) -> None:
        """Resolve a session's due waits and wake it once for all of them.

        Each used to start its own automatic turn, so two waits falling due together woke the
        agent twice, seconds apart, each with the same history to re-read.
        """
        event_bus = getattr(self._app.state, "event_bus", None)
        orchestrator = getattr(self._app.state, "orchestrator", None)
        store = getattr(self._app.state, "session_store", None)
        if event_bus is None or orchestrator is None or store is None:
            return
        for wait in waits:
            await event_bus.publish(
                SessionEvent(
                    session_id=wait.session_id,
                    event_type=EventType.WAIT_COMPLETED,
                    payload={
                        "wait_id": wait.id,
                        "wait_type": wait.kind,
                        "target": wait.target,
                        "status": "completed",
                        "summary": f"Wait {wait.id} reached deadline or completed.",
                    },
                )
            )
            await wait_store.resolve_wait(
                wait.session_id, wait.id, wait.tenant_org_id, status="completed"
            )
        _spawn_background_turn(
            session_id=waits[0].session_id,
            prompt=wake_prompt_for(waits),
            orchestrator=orchestrator,
            event_bus=event_bus,
            driver=getattr(self._app.state, "sandbox_driver", None),
            registry=getattr(self._app.state, "tool_registry", None),
            store=store,
            model=None,
            git_engine=getattr(self._app.state, "git_engine", None),
            db_mgr=getattr(self._app.state, "db_manager", None),
            concurrency_gate=getattr(self._app.state, "concurrency_gate", None),
            session_locks=getattr(self._app.state, "session_locks", None),
            synthetic_prompt=True,
        )


async def retention_policy_for(manager: DatabaseSessionManager, org_id: str) -> RetentionPolicy:
    """The retention windows an organization set under ``governance``; unset keeps forever.

    Without this the sweeper has no policy and deletes nothing, whatever the settings say.
    """
    settings, _ = await load_org_config(manager, org_id, "governance")
    return RetentionPolicy.from_settings(settings)


async def _refresh_remote_mcp_servers(
    manager: DatabaseSessionManager, cipher: CredentialCipher
) -> None:
    """Re-probe every remote MCP server; stdio commands are never spawned on a timer."""
    async with manager.session() as session:
        servers = (
            (
                await session.execute(
                    select(McpServerEntityModel).where(McpServerEntityModel.is_builtin.is_(False))
                )
            )
            .scalars()
            .all()
        )
        for server in servers:
            if canonical_transport(server.transport) in REMOTE_TRANSPORTS:
                await _refresh_one(server, cipher)
        await session.commit()


async def _refresh_one(server: McpServerEntityModel, cipher: CredentialCipher) -> None:
    try:
        status, tools, _ = await probe_and_fetch_tools(
            transport=server.transport,
            endpoint_or_command=server.endpoint_or_command,
            headers=auth_headers(server.encrypted_auth or {}, cipher),
            org_id=server.org_id,
        )
    except Exception as err:
        logger.debug("MCP refresh of %s failed: %s", server.id, err)
        return
    server.status = status
    server.last_probed = datetime.now(UTC).isoformat(timespec="seconds")
    if tools:
        server.tools = tools
