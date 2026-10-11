"""Embedded Slack Socket Mode assistant: dependency wiring and its background listener."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from agent_core.context import current_execution_ctx
from fastapi import FastAPI
from sandbox_driver.blobs import read_bytes
from slack_assistant.assistant import SlackAssistant
from slack_assistant.config import SlackAssistantSettings
from slack_assistant.policy import SlackOrgPolicy

from api.bootstrap.core import CoreServices
from api.db.session import current_tenant_org_id
from api.services.audit import AuditService
from api.services.cluster import LeaderElection
from api.services.decision_answer import decision_answer_prompt
from api.services.quota import SlackWorkspaceQuotaService
from api.services.settings import effective_settings
from api.services.slack_link import PairingCodes, find_member, workspace_org
from api.services.usage import month_start
from api.turns.gates import take_stored_question
from api.turns.tasks import run_guarded_turn
from specifications.interfaces.agent import TurnOverlay
from specifications.interfaces.events import EventType, SessionEvent

logger = logging.getLogger(__name__)


@dataclass
class SlackRuntime:
    """The assistant and the task that keeps its socket open."""

    assistant: SlackAssistant
    task: asyncio.Task[None]

    async def stop(self) -> None:
        if not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Shutting down Slack Assistant Socket Mode...")
        await self.assistant.stop_socket_mode()


def start_slack(
    app: FastAPI, core: CoreServices, settings: SlackAssistantSettings
) -> SlackRuntime | None:
    """Start the assistant when both Slack tokens are configured."""
    if not (settings.slack_bot_token and settings.slack_app_token):
        logger.info("Slack tokens not configured; skipping embedded Socket Mode background task.")
        return None
    logger.info("Initializing embedded Slack Socket Mode Assistant...")
    assistant = _build_assistant(app, core, settings)
    app.state.slack_assistant = assistant
    return SlackRuntime(assistant, asyncio.create_task(_listen(assistant, core.cluster_dsn)))


def _build_assistant(
    app: FastAPI, core: CoreServices, settings: SlackAssistantSettings
) -> SlackAssistant:
    callbacks = _SlackCallbacks(core)
    app.state.slack_pairing_codes = callbacks.pairing_codes
    return SlackAssistant(
        orchestrator=core.orchestrator,
        settings=settings,
        sandbox_driver=core.sandbox_driver,
        tool_registry=core.tool_registry,
        session_store=core.session_store,
        db_manager=core.db_manager,
        event_bus=core.event_bus,
        run_turn_fn=callbacks.run_turn,
        bind_tenant_fn=callbacks.bind_tenant,
        resolve_workspace_org_fn=callbacks.workspace_org,
        resolve_user_fn=callbacks.resolve_user,
        begin_pairing_fn=callbacks.begin_pairing,
        # Unauthenticated Slack is bounded before any turn starts and ingests files
        # through the same attachment pipeline as the web cockpit.
        quota_gate=SlackWorkspaceQuotaService(
            db_manager=core.db_manager, spend=callbacks.month_spend
        ),
        ingest_attachment_fn=core.attachment_service.store_bytes,
        resolve_slack_policy_fn=callbacks.resolve_policy,
        notifications_enabled_fn=callbacks.notifications_enabled,
        audit_fn=callbacks.audit,
        read_file_bytes_fn=callbacks.read_sandbox_file,
        answer_stored_question_fn=callbacks.answer_stored_question,
    )


async def _listen(assistant: SlackAssistant, cluster_dsn: str | None) -> None:
    try:
        if cluster_dsn:
            # Every replica learns the workspace identity; only the leader holds the
            # socket, otherwise each Slack event would be answered once per replica.
            await assistant.authenticate()
            logger.info("Slack Socket Mode runs on the elected leader replica only.")
            await LeaderElection(cluster_dsn, "slack-socket-mode").run(
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

    async def resolve_user(
        self, slack_user_id: str, email: str | None, org_id: str
    ) -> tuple[str, str] | None:
        """The Rocket member linked to a Slack user, within the workspace's own org."""
        try:
            return await find_member(self._core.db_manager, slack_user_id, org_id, email)
        except Exception as err:
            logger.warning("Error resolving slack user %s: %s", slack_user_id, err)
            return None

    async def begin_pairing(self, slack_user_id: str, team_id: str) -> str | None:
        if not self._core.db_manager:
            return None
        return await self.pairing_codes.begin(slack_user_id, team_id)

    async def month_spend(self, org_id: str) -> float:
        """Slack's share of the month's spend: its sessions are named ``slack_<thread>``."""
        return await self._core.usage_ledger.spend_usd(
            org_id, since=month_start(), session_prefix="slack_"
        )

    async def workspace_org(self, team_id: str) -> str | None:
        return await workspace_org(self._core.db_manager, team_id)

    async def run_turn(
        self, *, session_id: str, prompt: str, overlay: TurnOverlay, author_user_id: str
    ) -> None:
        """Run a Slack message through the same guarded turn the web cockpit uses."""
        core = self._core
        await run_guarded_turn(
            session_id=session_id,
            prompt=prompt,
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

    async def read_sandbox_file(self, session_id: str, path: str) -> bytes:
        return await read_bytes(self._core.sandbox_driver, session_id, path)

    async def resolve_policy(self, org_id: str) -> SlackOrgPolicy:
        """The org's Slack policy from the `slack` settings domain."""
        conf = await effective_settings(self._core.db_manager, org_id, "slack")
        return SlackOrgPolicy.from_settings(conf)

    async def notifications_enabled(self, org_id: str, user_id: str) -> bool:
        prefs = await effective_settings(self._core.db_manager, org_id, "user_preferences", user_id)
        return bool(prefs.get("slack_notifications_enabled", True))

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
