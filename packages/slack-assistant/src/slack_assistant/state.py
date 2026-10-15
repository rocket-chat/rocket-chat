"""The attributes every part of the Slack assistant shares.

``SlackAssistant`` is assembled from mixins (toolset, gates, admission, turn stream), each in
its own module. They all read the same collaborators and per-process caches, declared once
here so the type checker can follow them; ``SlackAssistant.__init__`` assigns them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from agent_core.tools.registry import ToolRegistry
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp, AsyncAssistant

from slack_assistant.config import SlackAssistantSettings
from slack_assistant.file_ingestion import FileDownloader
from slack_assistant.guardrails import FailureTracker, WorkspaceQuotaGate
from slack_assistant.health import EventDeduplicator, SocketHealth
from slack_assistant.policy import SlackOrgPolicy
from slack_assistant.ratelimit import BoundedRateLimiter
from specifications.interfaces.agent import AgentOrchestratorProtocol
from specifications.interfaces.events import EventBusProtocol
from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionStoreProtocol

RunTurnFn = Callable[..., Awaitable[None]]


class SlackAssistantState:
    settings: SlackAssistantSettings
    orchestrator: AgentOrchestratorProtocol
    sandbox_driver: SandboxDriverProtocol | None
    tool_registry: ToolRegistry | None
    quota_gate: WorkspaceQuotaGate | None
    event_bus: EventBusProtocol | None
    session_store: SessionStoreProtocol | None
    db_manager: Any
    resolve_user_fn: Callable[..., Any] | None
    app: AsyncApp
    assistant: AsyncAssistant
    handler: AsyncSocketModeHandler | None

    bot_user_id: str | None
    bot_user_name: str | None
    team_name: str | None
    team_id: str | None

    _clock: Callable[[], float]
    _ingest_attachment_fn: Callable[..., Any] | None
    _file_downloader: FileDownloader | None
    _run_turn_fn: RunTurnFn | None
    _bind_tenant_fn: Callable[[str], None] | None
    _resolve_workspace_org_fn: Callable[[str], Awaitable[str | None]] | None
    _begin_pairing_fn: Callable[[str, str], Awaitable[str | None]] | None
    _resolve_slack_policy_fn: Callable[[str], Awaitable[SlackOrgPolicy]] | None
    _notifications_enabled_fn: Callable[[str, str], Awaitable[bool]] | None
    _audit_fn: Callable[[str, dict[str, Any]], Awaitable[None]] | None
    _read_file_bytes_fn: Callable[[str, str], Awaitable[bytes]] | None
    # (session id, question id, chosen options, free text) -> the prompt that continues the
    # work, or None when no such question is stored (it was answered, or never existed).
    _answer_stored_question_fn: (
        Callable[[str, str, list[str], str | None], Awaitable[str | None]] | None
    )

    _gate_owners: dict[str, tuple[str, str | None]]
    _background: set[asyncio.Task[Any]]
    _session_policy: dict[str, tuple[str | None, set[str]]]
    _active_turns: set[str]
    _rate_counters: dict[str, int]
    _user_rate_limiter: BoundedRateLimiter | None
    _channel_rate_limiter: BoundedRateLimiter | None
    _failures: FailureTracker
    _last_activity: dict[str, float]
    _user_pairings: dict[str, str]
    _slack_user_cache: dict[str, dict[str, Any]]
    _session_meta: dict[str, tuple[str, str]]
    _slack_api_ready: bool
    _dedupe: EventDeduplicator
    _health: SocketHealth
    _watchdog: asyncio.Task[None] | None

    # Implemented by SlackAssistant itself; declared so the mixins can call them.
    def _bind_tenant(self, org_id: str) -> None:
        raise NotImplementedError

    async def _workspace_org(self, team_id: str) -> str:
        raise NotImplementedError

    async def _load_org_policy(self, org_id: str) -> SlackOrgPolicy:
        raise NotImplementedError

    def _session_link(self, org_policy: SlackOrgPolicy, session_id: str) -> str | None:
        raise NotImplementedError

    # Implemented by the toolset mixin; declared so the directory tools can call them.
    def _current_slack_context(self) -> tuple[str, str] | None:
        raise NotImplementedError

    def _outbound_channel_allowed(self, channel_id: str) -> bool:
        raise NotImplementedError

    async def _resolve_slack_user_name(self, user_id: str) -> str:
        raise NotImplementedError

    async def process_incoming_request(
        self,
        channel_id: str,
        thread_ts: str,
        raw_text: str,
        say_fn: Callable[..., Any],
        status_fn: Callable[..., Any] | None = None,
        user_id: str | None = None,
        channel_type: str | None = None,
        team_id: str | None = None,
        files: list[dict[str, Any]] | None = None,
        ephemeral_fn: Callable[..., Any] | None = None,
    ) -> None:
        raise NotImplementedError

    @staticmethod
    def get_session_id(channel_id: str, thread_ts: str) -> str:
        """Derive sanitized 1:1 session identifier compatible with Docker naming rules."""
        sanitized_ts = thread_ts.replace(".", "_")
        return f"slack_{channel_id}_{sanitized_ts}"
