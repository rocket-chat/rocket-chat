"""Embedded Slack Socket Mode assistant: dependency wiring and its background listener."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from agent_core.context import current_execution_ctx
from config_engine import Preset
from fastapi import FastAPI
from sandbox_driver.blobs import read_bytes
from slack_assistant.assistant import SlackAssistant
from slack_assistant.bot import ClassifyRequest, RouteDecision, SlackBot
from slack_assistant.config import SlackAssistantSettings
from slack_assistant.policy import SlackOrgPolicy
from slack_assistant.state import ClassifyFn

from api.bootstrap.core import CoreServices
from api.db.session import DatabaseSessionManager, current_tenant_org_id
from api.services.audit import AuditService
from api.services.budgets import BudgetService
from api.services.cluster import LeaderElection
from api.services.decision_answer import decision_answer_prompt
from api.services.queued_messages import hold_for_running_turn
from api.services.quota import SlackWorkspaceQuotaService
from api.services.settings import resolve_settings
from api.services.slack_bots import (
    SlackBotRecord,
    SlackBotStore,
    SlackTokens,
    runtime_bot,
)
from api.services.slack_link import (
    PairingCodes,
    ensure_slack_guest,
    find_member,
    slack_guest_id,
    workspace_org,
)
from api.services.slack_routing import classify_slack_message
from api.turns.gates import _forget_pending_decisions, take_stored_question
from api.turns.tasks import run_guarded_turn
from specifications.interfaces.agent import TurnOverlay
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.llm import ModelScope

logger = logging.getLogger(__name__)


@dataclass
class SlackRuntime:
    """One bot's assistant, the task that keeps its socket open, and the configuration it
    was started with (a change restarts it)."""

    assistant: SlackAssistant
    task: asyncio.Task[None]
    fingerprint: str = ""

    async def stop(self) -> None:
        if not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Shutting down Slack Assistant Socket Mode...")
        await self.assistant.stop_socket_mode()


RESYNC_SECONDS = 30.0
# Deprecated: bots are configured in the cockpit. Listed in the start-up warning when set.
_DEPRECATED_ENV = (
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_TEAM_ID",
    "SLACK_DEFAULT_ORG_ID",
    "SLACK_DM_POLICY",
    "SLACK_OUTBOUND_CHANNELS",
    "SLACK_ALLOWED_TOOLS",
    "SLACK_WEB_BASE_URL",
)


class SlackBotManager:
    """Runs one assistant per enabled bot of every organization.

    Every replica runs the same set (each learns its bot's identity), and per-bot leader
    election keeps one socket per bot across replicas. A change made in the cockpit restarts
    the bot at once on the replica that saved it, and within ``RESYNC_SECONDS`` elsewhere.
    """

    def __init__(
        self,
        app: FastAPI,
        core: CoreServices,
        bot_store: SlackBotStore,
        owner_db: DatabaseSessionManager | None = None,
        settings: SlackAssistantSettings | None = None,
    ) -> None:
        self._app = app
        self._core = core
        self._bot_store = bot_store
        self._owner_db = owner_db
        self._settings = settings or SlackAssistantSettings()
        self._callbacks = _SlackCallbacks(core)
        self._runtimes: dict[str, SlackRuntime] = {}
        self._lock = asyncio.Lock()
        self._resync: asyncio.Task[None] | None = None
        app.state.slack_pairing_codes = self._callbacks.pairing_codes

    def all(self) -> list[SlackAssistant]:
        return [r.assistant for r in self._runtimes.values()]

    def for_org(self, org_id: str) -> list[SlackAssistant]:
        return [a for a in self.all() if a.bot is not None and a.bot.org_id == org_id]

    def start(self) -> None:
        _warn_about_deprecated_env()
        self._resync = asyncio.create_task(self._resync_forever())

    async def sync(self) -> None:
        """Start new bots, restart changed ones and stop removed or disabled ones."""
        async with self._lock:
            ready = await self._bot_store.enabled_bots(self._owner_db)
            wanted = {record.id: (record, tokens) for record, tokens in ready}
            for bot_id in list(self._runtimes):
                runtime = self._runtimes[bot_id]
                found = wanted.get(bot_id)
                if found is None or runtime.fingerprint != _fingerprint(*found):
                    await self._stop_locked(bot_id)
            for bot_id, (record, tokens) in wanted.items():
                if bot_id not in self._runtimes:
                    self._start_locked(record, tokens)

    async def stop(self) -> None:
        if self._resync is not None:
            self._resync.cancel()
            with suppress(asyncio.CancelledError):
                await self._resync
        async with self._lock:
            for bot_id in list(self._runtimes):
                await self._stop_locked(bot_id)
        if self._owner_db is not None:
            await self._owner_db.close()

    async def _resync_forever(self) -> None:
        while True:
            try:
                await self.sync()
            except Exception as err:
                logger.warning("Slack bot synchronisation failed: %s", err)
            await asyncio.sleep(RESYNC_SECONDS)

    def _start_locked(self, record: SlackBotRecord, tokens: SlackTokens) -> None:
        assistant = _build_assistant(
            self._app,
            self._core,
            self._settings,
            bot=runtime_bot(record, tokens),
            classify_fn=self._callbacks.classify,
            callbacks=self._callbacks,
        )
        task = asyncio.create_task(
            _listen(assistant, self._core.cluster_dsn, lock_key=f"slack:{record.id}")
        )
        self._runtimes[record.id] = SlackRuntime(assistant, task, _fingerprint(record, tokens))
        logger.info("Started Slack bot %s (@%s)", record.id, record.handle or record.name)

    async def _stop_locked(self, bot_id: str) -> None:
        runtime = self._runtimes.pop(bot_id, None)
        if runtime is not None:
            await runtime.stop()


def _fingerprint(record: SlackBotRecord, tokens: SlackTokens) -> str:
    """What a running bot depends on: any change restarts it (tokens only as a hash)."""
    state = json.dumps(vars(record), sort_keys=True, default=str) + tokens.bot_token
    return hashlib.sha256((state + tokens.app_token).encode()).hexdigest()


def _warn_about_deprecated_env() -> None:
    present = [name for name in _DEPRECATED_ENV if os.getenv(name)]
    if present:
        logger.warning(
            "Deprecated Slack environment variables are set and ignored: %s. Configure Slack "
            "bots in Settings > Slack instead.",
            ", ".join(present),
        )


def start_slack(
    app: FastAPI, core: CoreServices, settings: SlackAssistantSettings
) -> SlackBotManager:
    """Run the organizations' Slack bots. Listing every organization's bots crosses tenants,
    so it reads on the schema-owner connection like the other cross-tenant jobs."""
    bot_store = SlackBotStore(core.db_manager)
    owner_db = (
        DatabaseSessionManager(os.getenv("ADMIN_DATABASE_URL") or core.database_url or "")
        if core.db_manager is not None
        else None
    )
    manager = SlackBotManager(app, core, bot_store, owner_db, settings)
    app.state.slack_bot_store = bot_store
    app.state.slack_bot_manager = manager
    manager.start()
    return manager


def _build_assistant(
    app: FastAPI,
    core: CoreServices,
    settings: SlackAssistantSettings | None = None,
    bot: SlackBot | None = None,
    classify_fn: ClassifyFn | None = None,
    callbacks: _SlackCallbacks | None = None,
) -> SlackAssistant:
    callbacks = callbacks or _SlackCallbacks(core)
    return SlackAssistant(
        orchestrator=core.orchestrator,
        settings=settings,
        sandbox_driver=core.sandbox_driver,
        tool_registry=core.tool_registry,
        session_store=core.session_store,
        db_manager=core.db_manager,
        event_bus=core.event_bus,
        run_turn_fn=callbacks.run_turn,
        hold_message_fn=callbacks.hold_message,
        bind_tenant_fn=callbacks.bind_tenant,
        resolve_workspace_org_fn=callbacks.workspace_org,
        resolve_user_fn=callbacks.resolve_user,
        guest_user_fn=callbacks.guest_user,
        begin_pairing_fn=callbacks.begin_pairing,
        # Unauthenticated Slack is bounded before any turn starts and ingests files
        # through the same attachment pipeline as the web cockpit.
        quota_gate=SlackWorkspaceQuotaService(
            db_manager=core.db_manager, spend=callbacks.workspace_over_limit
        ),
        ingest_attachment_fn=core.attachment_service.store_bytes,
        resolve_slack_policy_fn=lambda org_id: callbacks.resolve_policy(org_id, bot),
        audit_fn=callbacks.audit,
        read_file_bytes_fn=callbacks.read_sandbox_file,
        answer_stored_question_fn=callbacks.answer_stored_question,
        clear_stored_question_fn=callbacks.clear_stored_question,
        bot=bot,
        classify_fn=classify_fn,
    )


async def _listen(
    assistant: SlackAssistant, cluster_dsn: str | None, lock_key: str = "slack-socket-mode"
) -> None:
    try:
        if cluster_dsn:
            # Every replica learns the bot's identity; only the leader holds its socket,
            # otherwise each Slack event would be answered once per replica.
            await assistant.authenticate()
            logger.info("Slack Socket Mode runs on the elected leader only (lock %s).", lock_key)
            await LeaderElection(cluster_dsn, lock_key).run(
                assistant.start_socket_mode, assistant.stop_socket_mode
            )
        else:
            logger.info("Starting Slack Socket Mode background listener...")
            await assistant.start_socket_mode()
    except asyncio.CancelledError:
        logger.info("Slack Socket Mode listener cancelled.")
    except Exception as err:
        logger.error("Slack Socket Mode listener encountered an error: %s", err, exc_info=True)


class _SlackCallbacks:
    """The platform services the assistant calls back into, bound to one set of core services."""

    def __init__(self, core: CoreServices) -> None:
        self._core = core
        self.pairing_codes = PairingCodes(core.db_manager)
        self._audit_service = AuditService(core.db_manager)

    @staticmethod
    def bind_tenant(org_id: str) -> None:
        """Scope this task's database access to the Slack workspace's organization."""
        current_tenant_org_id.set(org_id)

    async def classify(self, request: ClassifyRequest) -> RouteDecision | None:
        """Route a message with the bot's classifier (billed to the bot's organization)."""
        return await classify_slack_message(
            self._core.orchestrator.gateway,
            request,
            self._core.db_manager,
            default_model=self._core.orchestrator.default_model,
        )

    async def resolve_user(
        self, slack_user_id: str, email: str | None, org_id: str
    ) -> tuple[str, str] | None:
        """The Rocket member linked to a Slack user, within the workspace's own org."""
        try:
            return await find_member(self._core.db_manager, slack_user_id, org_id, email)
        except Exception as err:
            logger.warning("Error resolving slack user %s: %s", slack_user_id, err)
            return None

    async def guest_user(self, org_id: str, team_id: str, slack_user_id: str | None) -> str:
        """The guest member that owns what an unlinked Slack user (or a shared thread) starts."""
        return await ensure_slack_guest(self._core.db_manager, org_id, team_id, slack_user_id)

    async def begin_pairing(self, slack_user_id: str, team_id: str) -> str | None:
        if not self._core.db_manager:
            return None
        return await self.pairing_codes.begin(slack_user_id, team_id)

    async def workspace_over_limit(self, org_id: str, team_id: str) -> bool:
        """Whether a block spend limit on this Slack workspace has no room left."""
        budgets = getattr(self._core.orchestrator.gateway, "budget_guard", None)
        if not isinstance(budgets, BudgetService):
            return False
        status = await budgets.status(
            ModelScope(org_id=org_id, user_id=slack_guest_id(team_id, None))
        )
        return any(
            item.limit.mode == "block"
            and item.limit.subject_kind == "slack_workspace"
            and item.remaining_usd <= 0
            for item in status.limits
        )

    async def workspace_org(self, team_id: str) -> str | None:
        return await workspace_org(self._core.db_manager, team_id)

    async def hold_message(
        self, *, session_id: str, prompt: str, author_user_id: str, message_id: str
    ) -> bool:
        """Keep a Slack message sent while a turn of its session runs, as the web app does.

        It is queued for after that turn (durably, so no restart or replica loses it), and the
        turn that takes it reaches the thread through the bot's delivery.
        """
        core = self._core
        record = await core.session_store.get_session(session_id, include_history=False)
        if record is None:
            return False
        return await hold_for_running_turn(
            session_store=core.session_store,
            orchestrator=core.orchestrator,
            event_bus=core.event_bus,
            db_mgr=core.db_manager,
            org_id=record.tenant_org_id,
            session_id=session_id,
            prompt=prompt,
            delivery="queue",
            message_id=message_id,
            author_user_id=author_user_id,
        )

    async def run_turn(
        self,
        *,
        session_id: str,
        prompt: str,
        overlay: TurnOverlay,
        author_user_id: str,
        message_id: str | None = None,
    ) -> None:
        """Run a Slack message through the same guarded turn the web cockpit uses."""
        core = self._core
        await run_guarded_turn(
            session_id=session_id,
            prompt=prompt,
            message_id=message_id,
            orchestrator=core.orchestrator,
            event_bus=core.event_bus,
            driver=core.sandbox_driver,
            registry=core.tool_registry,
            store=core.session_store,
            git_engine=core.git_engine,
            db_mgr=core.db_manager,
            author_user_id=author_user_id,
            attachment_service=core.attachment_service,
            concurrency_gate=core.concurrency_gate,
            session_locks=core.session_locks,
            overlay=overlay,
        )

    async def answer_stored_question(
        self, session_id: str, question_id: str, options: list[str], text: str | None
    ) -> str | None:
        """Continue a Slack thread whose turn died while it waited for an answer."""
        core = self._core
        record = await core.session_store.get_session(session_id)
        if record is None:
            return None
        stored = await take_stored_question(
            core.db_manager, session_id, record.tenant_org_id, question_id
        )
        if stored is None:
            return None
        await core.event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.DECISION_SUBMITTED,
                payload={
                    "question_id": question_id,
                    "selected_options": options,
                    "custom_text": text,
                },
            )
        )
        return decision_answer_prompt(
            str(stored.get("question_text") or "your question"), options, text
        )

    async def clear_stored_question(self, session_id: str, org_id: str) -> None:
        """Clear any pending question for this session in the database."""
        await _forget_pending_decisions(self._core.db_manager, session_id, org_id)

    async def read_sandbox_file(self, session_id: str, path: str) -> bytes:
        return await read_bytes(self._core.sandbox_driver, session_id, path)

    async def resolve_policy(self, org_id: str, bot: SlackBot | None = None) -> SlackOrgPolicy:
        """The org's Slack policy (the ``slack`` settings), with the bot's overrides as a
        preset: a key the organization locked keeps the organization's value."""
        presets = (Preset(f"slack_bot:{bot.id}", dict(bot.policy)),) if bot is not None else ()
        resolved = await resolve_settings(self._core.db_manager, org_id, "slack", presets=presets)
        return SlackOrgPolicy.from_settings(resolved.effective)

    async def audit(self, event_type: str, payload: dict[str, Any]) -> None:
        ctx = current_execution_ctx.get()
        await self._audit_service.record(
            "tool_call",
            org_id=ctx.tenant_org_id if ctx else "default_org",
            session_id=ctx.session_id if ctx else None,
            actor_user_id=ctx.tenant_user_id if ctx else None,
            tool_name=event_type,
            result_summary=str(payload)[:500],
            metadata=payload,
        )
