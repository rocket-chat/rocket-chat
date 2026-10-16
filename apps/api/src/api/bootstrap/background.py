"""Periodic and one-off background work owned by the application lifespan."""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from config_engine.crypto import CredentialCipher
from fastapi import FastAPI
from sqlalchemy import select

from api.bootstrap.core import CoreServices
from api.bootstrap.runtime import cluster_bus_enabled
from api.db.models import McpServerEntityModel
from api.db.session import DatabaseSessionManager
from api.services.audit import RetentionPolicy, RetentionSweeper
from api.services.cluster import LeaderElection
from api.services.mcp_engine import probe_and_fetch_tools
from api.services.mcp_runtime import REMOTE_TRANSPORTS, auth_headers, canonical_transport
from api.services.missions import MissionStore, claim_due
from api.services.settings import load_org_config
from api.services.turn_resume import resume_interrupted_turns
from api.services.turn_trace import (
    LEASE_HEARTBEAT_SECONDS,
    LEASE_TTL_SECONDS,
    beat_turn_leases,
    recover_interrupted_turns,
)
from api.services.workspace_release import (
    delay_resolver,
    idle_candidates,
    release_idle_workspaces,
)
from api.turns.tasks import launch_mission, resume_claimed_turn

logger = logging.getLogger(__name__)

SANDBOX_REAPER_SECONDS = 30
RETENTION_SWEEP_SECONDS = 3600
MCP_REFRESH_SECONDS = 900
MISSION_POLL_SECONDS = 30
WORKSPACE_RELEASE_SECONDS = 3600


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
        self._start_sandbox_reaper()
        if self._core.db_manager is None:
            return
        # Governance retention, turn leases, missions and MCP refresh sweep
        # every tenant, so they run on the schema-owner connection like migrations do.
        admin_manager = DatabaseSessionManager(
            os.getenv("ADMIN_DATABASE_URL") or self._core.database_url or ""
        )
        self._admin_manager = admin_manager
        self._start_retention(admin_manager)
        self._start_turn_recovery(admin_manager)
        self._start_missions(admin_manager)
        self._start_mcp_refresh(admin_manager)
        self._start_workspace_release(admin_manager)

    async def stop(self) -> None:
        for task in reversed(self._tasks):
            await _cancel(task)
        self._tasks.clear()
        if self._admin_manager is not None:
            await self._admin_manager.close()
            self._admin_manager = None

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        self._tasks.append(asyncio.ensure_future(coroutine))

    def _start_sandbox_reaper(self) -> None:
        driver = self._core.sandbox_driver
        if not hasattr(driver, "reconcile_inactivity"):
            return

        async def _session_is_live(session_id: str) -> bool:
            return await self._app.state.session_store.get_session(session_id) is not None

        async def _reap() -> None:
            await driver.reconcile_inactivity(is_live=_session_is_live)
            if hasattr(driver, "reconcile_warm_pool"):
                await driver.reconcile_warm_pool()

        self._spawn(_run_periodically("Sandbox background reaper", SANDBOX_REAPER_SECONDS, _reap))

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

        async def _resume_claimed_turns() -> None:
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
