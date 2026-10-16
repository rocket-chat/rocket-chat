"""Native Slack Assistant integration using 100% Socket Mode and Slack Bolt."""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from agent_core.tools.registry import ToolRegistry
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp, AsyncAssistant
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_bolt.context.say.async_say import AsyncSay
from slack_bolt.context.set_status.async_set_status import AsyncSetStatus
from slack_bolt.context.set_suggested_prompts.async_set_suggested_prompts import (
    AsyncSetSuggestedPrompts,
)
from slack_bolt.request.async_request import AsyncBoltRequest
from slack_bolt.response import BoltResponse as AsyncBoltResponse

from slack_assistant.admission import SlackAdmissionMixin
from slack_assistant.block_kit import SHARE_ACTION_ID
from slack_assistant.bot import SlackBot
from slack_assistant.config import SlackAssistantSettings
from slack_assistant.entrypoints import SlackEntrypointsMixin
from slack_assistant.file_ingestion import (
    FileDownloader,
    SlackHttpFileDownloader,
    extract_uploaded_files,
    ingest_slack_files,
)
from slack_assistant.gates import SlackGatesMixin
from slack_assistant.guardrails import (
    FailureTracker,
    WorkspaceQuotaGate,
)
from slack_assistant.health import EventDeduplicator, SocketHealth
from slack_assistant.policy import ChannelProfile, SlackOrgPolicy
from slack_assistant.ratelimit import BoundedRateLimiter
from slack_assistant.routing import ROUTE_KEY, Routing, SlackRoutingMixin
from slack_assistant.state import ClassifyFn, RunTurnFn, SlackAssistantState
from slack_assistant.stream import TurnStream
from slack_assistant.toolset import SlackToolsetMixin
from slack_assistant.turn_stream import SlackTurnStreamMixin, TurnOutcome, TurnView
from specifications.interfaces.agent import (
    AgentOrchestratorProtocol,
    TurnOverlay,
)
from specifications.interfaces.events import EventBusProtocol
from specifications.interfaces.llm import AttachmentRef
from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import (
    SessionRecord,
    SessionSource,
    SessionStoreProtocol,
)

logger = logging.getLogger(__name__)


def clean_mention_text(text: str) -> str:
    """Remove Slack user/bot mention markup from message content."""
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


@dataclass(frozen=True)
class _Admitted:
    """A request that passed admission: who it is for and under which policy it runs."""

    prompt: str
    # The request as the person wrote it, without the speaker attribution: what titles use.
    title_text: str
    team_scope: str
    is_dm: bool
    session_user_id: str
    session_org_id: str
    rocket_user_id: str
    org_policy: SlackOrgPolicy
    sandbox_image: str = ""
    team_id: str = ""
    workspace_org: str = ""
    rocket_org_id: str = ""
    # Private response mode: only the requester sees the work and the answer.
    private: bool = False


class SlackAssistant(
    SlackEntrypointsMixin,
    SlackToolsetMixin,
    SlackGatesMixin,
    SlackAdmissionMixin,
    SlackRoutingMixin,
    SlackTurnStreamMixin,
    SlackAssistantState,
):
    """
    Manages Slack Assistant lifecycle, Socket Mode connection, Block Kit rendering,
    and bidirectional session mapping to Docker sandbox workspaces.
    """

    def __init__(
        self,
        orchestrator: AgentOrchestratorProtocol,
        settings: SlackAssistantSettings | None = None,
        sandbox_driver: SandboxDriverProtocol | None = None,
        tool_registry: ToolRegistry | None = None,
        session_store: SessionStoreProtocol | None = None,
        db_manager: Any = None,
        event_bus: EventBusProtocol | None = None,
        run_turn_fn: RunTurnFn | None = None,
        bind_tenant_fn: Callable[[str], None] | None = None,
        resolve_workspace_org_fn: Callable[[str], Awaitable[str | None]] | None = None,
        resolve_user_fn: Callable[..., Any] | None = None,
        guest_user_fn: Callable[[str, str, str | None], Awaitable[str]] | None = None,
        begin_pairing_fn: Callable[[str, str], Awaitable[str | None]] | None = None,
        resolve_slack_policy_fn: Callable[[str], Awaitable[SlackOrgPolicy]] | None = None,
        audit_fn: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        read_file_bytes_fn: Callable[[str, str], Awaitable[bytes]] | None = None,
        answer_stored_question_fn: Callable[
            [str, str, list[str], str | None], Awaitable[str | None]
        ]
        | None = None,
        quota_gate: WorkspaceQuotaGate | None = None,
        clock: Callable[[], float] | None = None,
        ingest_attachment_fn: Callable[..., Any] | None = None,
        file_downloader: FileDownloader | None = None,
        app: AsyncApp | None = None,
        handler: AsyncSocketModeHandler | None = None,
        bot: SlackBot | None = None,
        classify_fn: ClassifyFn | None = None,
    ) -> None:
        base_settings = settings or SlackAssistantSettings()
        # A bot configured in the cockpit brings its own Slack app; the deployment's settings
        # keep only the limits the operator sets for every bot.
        self.settings: SlackAssistantSettings = (
            base_settings.model_copy(
                update={"slack_bot_token": bot.bot_token, "slack_app_token": bot.app_token}
            )
            if bot is not None
            else base_settings
        )
        self.bot: SlackBot | None = bot
        self._classify_fn: ClassifyFn | None = classify_fn
        self.orchestrator: AgentOrchestratorProtocol = orchestrator
        self.sandbox_driver: SandboxDriverProtocol | None = sandbox_driver
        self.tool_registry: ToolRegistry | None = tool_registry
        # Unauthenticated-path hardening: the per-workspace quota gate is
        # injected by the API layer (a package cannot import application code), the token
        # buckets are built from settings here so they exist even without the gate, and the
        # clock is injectable so tests advance time instead of sleeping.
        self.quota_gate: WorkspaceQuotaGate | None = quota_gate
        self._clock: Callable[[], float] = clock or time.time
        self._ingest_attachment_fn = ingest_attachment_fn
        self._file_downloader: FileDownloader | None = file_downloader
        # Injected by the API layer: the shared turn pipeline (the same one the web cockpit
        # uses) and the bus its events are published on. Slack only renders them.
        self.event_bus: EventBusProtocol | None = event_bus
        self._run_turn_fn = run_turn_fn
        self._bind_tenant_fn = bind_tenant_fn
        self._resolve_workspace_org_fn = resolve_workspace_org_fn
        self._begin_pairing_fn = begin_pairing_fn
        # Who may answer each pending question/approval: (session id, requesting Slack user).
        self._gate_owners: dict[str, tuple[str, str | None]] = {}
        self._background: set[asyncio.Task[Any]] = set()
        # Injected by the API layer: the org's outbound Slack policy (DM policy + channel
        # allowlist), resolved per turn and stored per session.
        self._resolve_slack_policy_fn = resolve_slack_policy_fn
        self._session_policy: dict[str, tuple[str | None, set[str]]] = {}
        # Injected by the API layer: durable audit for outbound Slack actions.
        self._audit_fn = audit_fn
        # Reads a sandbox file for upload (screenshots); injected by the API layer.
        self._read_file_bytes_fn = read_file_bytes_fn
        self._answer_stored_question_fn = answer_stored_question_fn
        self._active_turns: set[str] = set()
        self._stopped_by: dict[str, str] = {}
        self._rate_counters: dict[str, int] = {
            "rate_limit_user_denied": 0,
            "rate_limit_channel_denied": 0,
            "rate_limit_admitted": 0,
        }
        self._user_rate_limiter: BoundedRateLimiter | None = (
            BoundedRateLimiter(
                capacity=float(self.settings.slack_user_turns_per_minute),
                refill_per_second=float(self.settings.slack_user_turns_per_minute) / 60.0,
            )
            if self.settings.slack_user_turns_per_minute > 0
            else None
        )
        self._channel_rate_limiter: BoundedRateLimiter | None = (
            BoundedRateLimiter(
                capacity=float(self.settings.slack_channel_turns_per_minute),
                refill_per_second=float(self.settings.slack_channel_turns_per_minute) / 60.0,
            )
            if self.settings.slack_channel_turns_per_minute > 0
            else None
        )
        self._failures = FailureTracker(
            threshold=self.settings.slack_max_consecutive_failures,
            cooldown_seconds=self.settings.slack_failure_cooldown_seconds,
        )
        self.session_store: SessionStoreProtocol | None = session_store
        self.db_manager: Any = db_manager
        self.resolve_user_fn: Callable[..., Any] | None = resolve_user_fn
        # (org, team, Slack user or None for the workspace) -> the guest member that owns
        # what an unlinked user, or a shared channel thread, starts.
        self.guest_user_fn = guest_user_fn

        self._last_activity: dict[str, float] = {}
        self._user_pairings: dict[str, str] = {}
        self._slack_user_cache: dict[str, dict[str, Any]] = {}
        self._session_meta: dict[str, tuple[str, str]] = {}
        self._slack_api_ready: bool = False
        self.bot_user_id: str | None = None
        self.bot_user_name: str | None = None
        self.team_name: str | None = None
        self.team_id: str | None = None

        bot_token = self.settings.slack_bot_token or "xoxb-test-placeholder"
        self.app: AsyncApp = app or AsyncApp(token=bot_token)
        self.assistant: AsyncAssistant = AsyncAssistant()
        self.handler: AsyncSocketModeHandler | None = handler

        self._dedupe = EventDeduplicator(clock=self._clock)
        self._health = SocketHealth()
        self._watchdog = None
        self._register_handlers()
        self._register_entrypoints()
        self._register_slack_context_tools()
        self._go_live()

    # -- Slack toolset: discovery + outbound ------------------------------------------

    def _register_handlers(self) -> None:
        """Register Slack Bolt Assistant listeners, mention events, and action gates."""
        self._register_middleware()
        self._register_assistant_panel()
        self._register_message_events()
        self._register_gate_actions()
        self.app.assistant(self.assistant)

    def _register_middleware(self) -> None:
        @self.app.middleware
        async def drop_redelivered_events(
            req: AsyncBoltRequest, resp: AsyncBoltResponse, next_: Callable[[], Awaitable[Any]]
        ) -> None:
            # Slack redelivers an event whose acknowledgement came late; handling it twice
            # would answer twice. Commands, shortcuts and clicks carry a unique trigger id.
            body = req.body if isinstance(req.body, dict) else {}
            key = body.get("event_id") or body.get("trigger_id")
            self._health.event_seen(self._clock())
            if key and not self._dedupe.first_time(str(key)):
                self._health.redelivered_events += 1
                resp.status = 200
                resp.body = ""
                return
            await next_()

    def _register_assistant_panel(self) -> None:
        @self.assistant.thread_started
        async def handle_thread_started(
            say: AsyncSay,
            set_suggested_prompts: AsyncSetSuggestedPrompts,
            event: dict[str, Any],
        ) -> None:
            thread_ts = event.get("thread_ts") or event.get("ts", "")
            await say(
                text=(
                    "Hello! I'm your Rocket Chat coding assistant. "
                    "I have access to your repositories and an isolated sandbox. "
                    "What are we building or fixing today?"
                ),
                thread_ts=thread_ts,
            )
            await set_suggested_prompts(
                prompts=[
                    {
                        "title": "Run test suite",
                        "message": "Run the project test suite and report any failures",
                    },
                    {
                        "title": "Fix open issue",
                        "message": "Investigate and resolve the latest open GitHub issue",
                    },
                    {
                        "title": "Explain architecture",
                        "message": "Give me a high-level overview of this codebase",
                    },
                ],
                thread_ts=thread_ts,
            )

    def _register_message_events(self) -> None:
        @self.assistant.user_message
        async def handle_user_message(
            say: AsyncSay,
            set_status: AsyncSetStatus,
            event: dict[str, Any],
        ) -> None:
            channel_id = event.get("channel", "")
            thread_ts = event.get("thread_ts") or event.get("ts", "")
            text = event.get("text", "")
            user_id = event.get("user", "")
            channel_type = event.get("channel_type")
            if not self.should_respond_to_thread_message(text, channel_type, user_id):
                return
            if not self._answers_direct_messages():
                return
            await self.process_incoming_request(
                channel_id=channel_id,
                thread_ts=thread_ts,
                raw_text=text,
                say_fn=say,
                status_fn=set_status,
                user_id=user_id,
                channel_type=channel_type,
                team_id=event.get("team") or event.get("team_id"),
                files=event.get("files"),
                message_ts=event.get("ts"),
            )

        @self.app.event("app_mention")
        async def handle_app_mention(
            say: AsyncSay,
            event: dict[str, Any],
            context: dict[str, Any],
        ) -> None:
            channel_id = event.get("channel", "")
            thread_ts = event.get("thread_ts") or event.get("ts", "")
            text = event.get("text", "")
            user_id = event.get("user", "")
            channel_type = event.get("channel_type") or "channel"
            status_fn = context.get("set_status")
            if not status_fn and thread_ts:
                status_fn = AsyncSetStatus(
                    client=self.app.client,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                )
            await self.process_incoming_request(
                channel_id=channel_id,
                thread_ts=thread_ts,
                raw_text=text,
                say_fn=say,
                status_fn=status_fn,
                user_id=user_id,
                channel_type=channel_type,
                team_id=event.get("team") or event.get("team_id"),
                files=event.get("files"),
                message_ts=event.get("ts"),
            )

        @self.app.event("message")
        async def handle_message(
            say: AsyncSay,
            event: dict[str, Any],
            context: dict[str, Any],
        ) -> None:
            # Ignore messages from bots or system subtypes to avoid loops
            if event.get("bot_id") or event.get("subtype"):
                return

            if event.get("channel_type") == "im":
                if self._answers_direct_messages():
                    await self._handle_event_request(event, context, say)
            elif self._listens_to(event):
                await self._handle_event_request(event, context, say)

    def _answers_direct_messages(self) -> bool:
        return self.bot is None or self.bot.respond_in_dms

    def _listens_to(self, event: dict[str, Any]) -> bool:
        """A new message (not a thread reply) in a channel the bot listens to, without a
        mention of the bot (a mention arrives as ``app_mention``)."""
        if self.bot is None or event.get("channel", "") not in self.bot.listen_channels:
            return False
        if event.get("thread_ts") and event.get("thread_ts") != event.get("ts"):
            return False
        return not (self.bot_user_id and f"<@{self.bot_user_id}>" in str(event.get("text", "")))

    async def _handle_event_request(
        self, event: dict[str, Any], context: dict[str, Any], say: AsyncSay
    ) -> None:
        channel_id = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts", "")
        status_fn = context.get("set_status")
        if not status_fn and thread_ts:
            status_fn = AsyncSetStatus(
                client=self.app.client, channel_id=channel_id, thread_ts=thread_ts
            )
        await self.process_incoming_request(
            channel_id=channel_id,
            thread_ts=thread_ts,
            raw_text=event.get("text", ""),
            say_fn=say,
            status_fn=status_fn,
            user_id=event.get("user", ""),
            channel_type=event.get("channel_type"),
            team_id=event.get("team") or event.get("team_id"),
            files=event.get("files"),
            message_ts=event.get("ts"),
        )

    def _register_gate_actions(self) -> None:
        @self.app.action("approve_action")
        async def handle_approve_action(ack: AsyncAck, body: dict[str, Any], say: AsyncSay) -> None:
            await ack()
            await self._resolve_approval(body, say, approved=True)

        @self.app.action("reject_action")
        async def handle_reject_action(ack: AsyncAck, body: dict[str, Any], say: AsyncSay) -> None:
            await ack()
            await self._resolve_approval(body, say, approved=False)

        @self.app.event("agent_session_stopped")
        async def handle_agent_session_stopped(event: dict[str, Any], body: dict[str, Any]) -> None:
            await self._stop_agent_session(event, body)

        @self.app.action(SHARE_ACTION_ID)
        async def handle_share(ack: AsyncAck, body: dict[str, Any]) -> None:
            await ack()
            await self._share_private_answer(body)

        @self.app.action("open_private_session")
        async def handle_open_session(ack: AsyncAck) -> None:
            await ack()

        @self.app.action(re.compile(r"^question_option_select"))
        async def handle_question_option(
            ack: AsyncAck, body: dict[str, Any], say: AsyncSay
        ) -> None:
            await ack()
            await self._resolve_question(body, say)

    async def _ingest_slack_attachments(
        self,
        *,
        files: list[dict[str, Any]],
        session_id: str,
        org_id: str,
        user_id: str,
        channel_id: str,
        thread_ts: str,
        is_dm: bool,
        ephemeral_fn: Callable[..., Any] | None,
        say_fn: Callable[..., Any],
    ) -> tuple[str, tuple[AttachmentRef, ...]]:
        """Download attached Slack files and delegate them to the shared service.

        Returns a note for the dynamic prompt listing where each file landed, plus the
        ingested attachments (so images reach the model as vision blocks). Over-cap files
        are reported back to the user instead of being dropped silently.
        """
        if self._ingest_attachment_fn is None:
            return "", ()
        uploads = extract_uploaded_files({"files": files})
        if not uploads:
            return "", ()
        downloader = self._file_downloader or SlackHttpFileDownloader(
            self.settings.slack_bot_token,
            self.settings.slack_file_download_timeout_seconds,
        )
        result = await ingest_slack_files(
            files=uploads,
            session_id=session_id,
            org_id=org_id,
            user_id=user_id,
            max_attachments=self.settings.slack_max_attachments_per_turn,
            downloader=downloader,
            ingest_attachment_fn=self._ingest_attachment_fn,
        )
        if result.ignored:
            await self._send_ephemeral(
                text=(
                    f"📎 {result.ignored} attached file(s) were skipped: this workspace "
                    f"ingests at most {self.settings.slack_max_attachments_per_turn} per turn."
                ),
                channel_id=channel_id,
                user_id=user_id,
                thread_ts=thread_ts,
                is_dm=is_dm,
                ephemeral_fn=ephemeral_fn,
                fallback_say=say_fn,
            )
        for failure in result.failures:
            logger.warning("Slack attachment ingestion failed: %s", failure)
        if not result.attachments:
            return "", ()
        lines = [f"- {a.name} ({a.mime}) → {a.workspace_path}" for a in result.attachments]
        note = (
            "Slack files uploaded with this message (read them from the workspace):\n"
            + "\n".join(lines)
        )
        return note, tuple(result.attachments)

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
        message_ts: str | None = None,
    ) -> None:
        """Admit one Slack message, route it, run it through the shared turn pipeline, render
        the result.

        Admission (channel policy, quota, rate limits), routing and rendering (spinners, Block
        Kit, chunked replies) are Slack's. The turn itself - persona, model routing, tools,
        approvals, tracing, audit, redaction, persistence - is the same pipeline the web
        cockpit uses, so a Slack session is a first-class, cockpit-visible session.
        """
        self._last_activity[self.get_session_id(channel_id, thread_ts)] = time.time()
        admitted = await self._admit(
            channel_id=channel_id,
            thread_ts=thread_ts,
            raw_text=raw_text,
            say_fn=say_fn,
            user_id=user_id,
            channel_type=channel_type,
            team_id=team_id,
            ephemeral_fn=ephemeral_fn,
        )
        if admitted is None:
            return
        await self._safely_update_status(status_fn, "Analyzing request...")
        routing = await self._route_request(
            org_id=admitted.workspace_org,
            text=admitted.title_text,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            org_policy=admitted.org_policy,
        )
        if routing is None:
            await self._safely_update_status(status_fn, "")
            return
        admitted = await self._apply_routing(admitted, routing, user_id)
        session_id = self.get_session_id(
            channel_id, thread_ts, private_for=user_id if admitted.private else None
        )
        self._last_activity[session_id] = time.time()
        self._session_meta[session_id] = (channel_id, thread_ts)
        self._session_policy[session_id] = (
            admitted.org_policy.dm_policy or None,
            set(admitted.org_policy.outbound_channels),
        )
        await self._ensure_session_record(
            session_id=session_id,
            admitted=admitted,
            channel_id=channel_id,
            thread_ts=thread_ts,
            slack_user_id=user_id,
            route=routing.route,
        )
        view = TurnView(
            session_id=session_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            requester_slack_id=user_id,
            say_fn=self._ephemeral_say(channel_id, user_id or "") if admitted.private else say_fn,
            status_fn=None if admitted.private else status_fn,
            outcome=TurnOutcome(),
            link_url=self._session_link(admitted.org_policy, session_id),
            private=admitted.private,
        )
        if admitted.private:
            await self._safely_update_status(status_fn, "")
            await self._acknowledge_privately(
                channel_id=channel_id,
                message_ts=message_ts,
                say=view.say_fn,
                thread_ts=thread_ts,
                link_url=view.link_url,
            )
        attachment_note, attachments = await self._attachments_for(
            files, admitted, view, ephemeral_fn
        )
        overlay = await self._build_overlay(
            channel_id=channel_id,
            thread_ts=thread_ts,
            admitted=admitted,
            attachment_note=attachment_note,
            attachments=attachments,
        )
        await self._run_and_report(view, admitted, overlay)

    async def _apply_routing(
        self, admitted: _Admitted, routing: Routing, user_id: str | None
    ) -> _Admitted:
        """The routed agent, and for a private request the requester's own session."""
        policy = admitted.org_policy
        if routing.agent_id:
            policy = replace(policy, default_agent_persona=routing.agent_id)
        if routing.response_mode != "private" or admitted.is_dm or not user_id:
            return replace(admitted, org_policy=policy)
        # The requester's own account when it belongs to this organization, else their guest:
        # never the workspace's shared guest, so the session is theirs alone.
        owner = (
            admitted.rocket_user_id
            if admitted.rocket_org_id == admitted.workspace_org
            else await self._guest(admitted.workspace_org, admitted.team_id, user_id)
        )
        return replace(
            admitted,
            org_policy=policy,
            private=True,
            session_user_id=owner,
            session_org_id=admitted.workspace_org,
        )

    async def _admit(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        raw_text: str,
        say_fn: Callable[..., Any],
        user_id: str | None,
        channel_type: str | None,
        team_id: str | None,
        ephemeral_fn: Callable[..., Any] | None,
    ) -> _Admitted | None:
        """Who is asking, under which organization, and is the request allowed at all."""
        prompt = clean_mention_text(raw_text) or "Hello! How can I help you today?"
        title_text = prompt
        resolved_team_id = team_id or self.team_id or self.settings.slack_team_id or ""
        is_dm = channel_type == "im"
        workspace_org = await self._workspace_org(resolved_team_id)
        org_policy = await self._load_org_policy(workspace_org)
        if not org_policy.allows_channel_type(channel_type):
            logger.info("Ignoring message in disallowed Slack channel type %s", channel_type)
            return None

        rocket_user_id, rocket_org_id = await self._resolve_identity(
            user_id, workspace_org, org_policy, resolved_team_id
        )
        if user_id:
            speaker_name = await self._resolve_slack_user_name(user_id)
            prompt = f"[Speaker: {speaker_name} (<@{user_id}>)] {prompt}"

        # Private DMs belong to the paired member; shared channel threads belong to the
        # workspace and never borrow a member's credentials.
        session_user_id = (
            rocket_user_id if is_dm else await self._guest(workspace_org, resolved_team_id, None)
        )
        session_org_id = rocket_org_id if is_dm else workspace_org
        self._bind_tenant(session_org_id)
        if session_org_id != workspace_org:
            org_policy = await self._load_org_policy(session_org_id)

        denial = await self._pre_dispatch_guard(
            team_id=resolved_team_id,
            session_id=self.get_session_id(channel_id, thread_ts),
            org_id=session_org_id,
            user_id=user_id,
            channel_id=channel_id,
        )
        if denial is not None:
            await self._send_ephemeral(
                text=self._denial_text(denial),
                channel_id=channel_id,
                user_id=user_id,
                thread_ts=thread_ts,
                is_dm=is_dm,
                ephemeral_fn=ephemeral_fn,
                fallback_say=say_fn,
            )
            return None
        profile = org_policy.profile_for(channel_id)
        return _Admitted(
            prompt=prompt,
            title_text=title_text,
            team_scope=resolved_team_id or self.get_session_id(channel_id, thread_ts),
            is_dm=is_dm,
            session_user_id=session_user_id,
            session_org_id=session_org_id,
            rocket_user_id=rocket_user_id,
            org_policy=org_policy,
            sandbox_image=profile.sandbox_image if profile and not is_dm else "",
            team_id=resolved_team_id,
            workspace_org=workspace_org,
            rocket_org_id=rocket_org_id,
        )

    async def _attachments_for(
        self,
        files: list[dict[str, Any]] | None,
        admitted: _Admitted,
        view: TurnView,
        ephemeral_fn: Callable[..., Any] | None,
    ) -> tuple[str, tuple[AttachmentRef, ...]]:
        if not files:
            return "", ()
        return await self._ingest_slack_attachments(
            files=files,
            session_id=view.session_id,
            org_id=admitted.session_org_id,
            user_id=admitted.session_user_id,
            channel_id=view.channel_id,
            thread_ts=view.thread_ts,
            is_dm=admitted.is_dm,
            ephemeral_fn=ephemeral_fn,
            say_fn=view.say_fn,
        )

    def _session_link(self, org_policy: SlackOrgPolicy, session_id: str) -> str | None:
        base = (org_policy.web_base_url or self.settings.slack_web_base_url).rstrip("/")
        return f"{base}/chat/{session_id}" if base else None

    async def _run_and_report(
        self, view: TurnView, admitted: _Admitted, overlay: TurnOverlay
    ) -> None:
        """Run the turn, its steps streamed into its reply, then post the result."""
        session_id = view.session_id
        await self._remember_requester(session_id, admitted.session_org_id, view.requester_slack_id)
        if admitted.org_policy.live_progress and self._slack_api_ready and not view.private:
            view.stream = TurnStream(
                self.app.client,
                view.channel_id,
                view.thread_ts,
                recipient_user_id=view.requester_slack_id,
                recipient_team_id=admitted.team_id or None,
                clock=self._clock,
            )
        self._active_turns.add(session_id)
        error: str | None = None
        try:
            await self._run_shared_turn(
                view,
                prompt=admitted.prompt,
                overlay=overlay,
                author_user_id=admitted.rocket_user_id,
            )
            error = view.outcome.error
        except Exception as err:
            error = str(err)
            logger.error("Slack turn failed for session %s: %s", session_id, err, exc_info=True)
        finally:
            self._active_turns.discard(session_id)
            await self._safely_update_status(view.status_fn, "")
            # The legacy status call does not end an agent session; left "processing", Slack
            # keeps its working indicator (and a dead stop button) under the thread.
            if not view.private:
                await self._set_session_status(view.channel_id, view.thread_ts, "active")
        if await self._report_stop(view):
            return
        if error:
            self._failures.record_failure(admitted.team_scope, self._clock())
            notice = f"⚠️ I encountered an error while processing your request: {error}"
            if view.stream is None or not await view.stream.finish(notice, []):
                await view.say_fn(text=notice, thread_ts=view.thread_ts)
            return
        self._failures.record_success(admitted.team_scope)
        if view.private:
            await self._post_private_result(
                session_id=session_id,
                org_id=admitted.session_org_id,
                answer=view.outcome.text,
                say=view.say_fn,
                thread_ts=view.thread_ts,
                requester=view.requester_slack_id or "",
                link_url=view.link_url,
            )
            return
        await self._post_result(view, admitted.org_policy)

    async def _thread_permalink(self, channel_id: str, thread_ts: str) -> str:
        """The thread's link in Slack, for "Open in Slack" in the web app."""
        if not self._slack_api_ready:
            return ""
        try:
            response = await self.app.client.chat_getPermalink(
                channel=channel_id, message_ts=thread_ts
            )
        except Exception as err:
            logger.debug("No permalink for %s/%s: %s", channel_id, thread_ts, err)
            return ""
        return str(response.get("permalink") or "")

    async def _report_stop(self, view: TurnView) -> bool:
        """End a turn its requester stopped from Slack: say so, and leave "processing"."""
        stopped_by = self._stopped_by.pop(view.session_id, None)
        if stopped_by is None:
            return False
        # A streamed reply is markdown, where Slack does not turn ":stop_button:" into an emoji.
        notice = f"⏹️ Stopped by <@{stopped_by}>."
        if view.stream is None or not await view.stream.finish(notice, [], failed=True):
            await view.say_fn(text=notice, thread_ts=view.thread_ts)
        await self._set_session_status(view.channel_id, view.thread_ts, "active")
        return True

    async def _remember_requester(
        self, session_id: str, org_id: str, slack_user_id: str | None
    ) -> None:
        """Record who started this turn on the session, so any replica can check who may answer."""
        if self.session_store is None or not slack_user_id:
            return

        def remember(record: SessionRecord) -> None:
            known = record.metadata if isinstance(record.metadata, dict) else {}
            record.metadata = {**known, "slack_turn_requester": slack_user_id}

        try:
            await self.session_store.mutate_session(session_id, org_id, remember)
        except Exception as err:
            logger.warning("Could not record the Slack requester of %s: %s", session_id, err)

    async def _workspace_org(self, team_id: str) -> str:
        """The organization that owns this Slack workspace (bound by an administrator)."""
        if self.bot is not None:
            return self.bot.org_id
        if self._resolve_workspace_org_fn is not None and team_id:
            try:
                bound = await self._resolve_workspace_org_fn(team_id)
            except Exception as err:
                logger.warning("Workspace->organization lookup failed for %s: %s", team_id, err)
                bound = None
            if bound:
                return str(bound)
        return self.settings.slack_default_org_id

    def _bind_tenant(self, org_id: str) -> None:
        if self._bind_tenant_fn is not None:
            self._bind_tenant_fn(org_id)

    async def _guest(self, org_id: str, team_id: str, slack_user_id: str | None) -> str:
        """The guest member for an unlinked Slack user, or for the workspace's shared threads.

        Without the platform's callback (tests, no database) the bare Slack identity is used.
        """
        if self.guest_user_fn is None:
            return slack_user_id or "slack_global"
        try:
            return await self.guest_user_fn(org_id, team_id, slack_user_id)
        except Exception as err:
            logger.warning("Could not provision the Slack guest for %s: %s", slack_user_id, err)
            return slack_user_id or "slack_global"

    async def _resolve_identity(
        self,
        slack_user_id: str | None,
        workspace_org: str,
        org_policy: SlackOrgPolicy,
        team_id: str = "",
    ) -> tuple[str, str]:
        """The Rocket member behind a Slack user, or the anonymous workspace identity.

        Only a pairing the member confirmed from their authenticated session, or (when the
        organization enables it) a match on the member's e-mail inside this workspace's own
        organization, links the two - never an e-mail typed in Slack.
        """
        if not slack_user_id:
            return "slack_user", workspace_org
        resolved = None
        if self.resolve_user_fn is not None:
            resolved = await self.resolve_user_fn(slack_user_id, None, workspace_org)
            if not resolved and org_policy.auto_link_by_verified_email:
                profile = await self._fetch_slack_user(slack_user_id)
                email = (profile or {}).get("profile", {}).get("email") if profile else None
                if email:
                    resolved = await self.resolve_user_fn(slack_user_id, email, workspace_org)
        if not resolved:
            return await self._guest(workspace_org, team_id, slack_user_id), workspace_org
        member_id, member_org = resolved
        self._user_pairings[slack_user_id] = member_id
        return member_id, member_org

    async def _ensure_session_record(
        self,
        *,
        session_id: str,
        admitted: _Admitted,
        channel_id: str,
        thread_ts: str,
        slack_user_id: str | None,
        route: str = "",
    ) -> None:
        """Create the thread's cockpit-visible session on first contact."""
        if self.session_store is None:
            return
        try:
            existing = await self.session_store.get_session(session_id)
            if existing is not None:
                if route and (existing.metadata or {}).get(ROUTE_KEY) != route:
                    await self.session_store.mutate_session(
                        session_id, admitted.session_org_id, _with_route(route)
                    )
                return
            record = _new_session_record(session_id, admitted, channel_id, thread_ts, slack_user_id)
            permalink = await self._thread_permalink(channel_id, thread_ts)
            if permalink:
                record.metadata["slack_permalink"] = permalink
            await self.session_store.create_session(record)
            if route:
                await self.session_store.mutate_session(
                    session_id, admitted.session_org_id, _with_route(route)
                )
        except Exception as err:
            logger.warning("Failed saving Slack session %s: %s", session_id, err)

    async def _build_overlay(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        admitted: _Admitted,
        attachment_note: str,
        attachments: tuple[AttachmentRef, ...],
    ) -> TurnOverlay:
        """Slack's additions to the shared prompt, tools and attachments for this turn."""
        is_dm, org_policy = admitted.is_dm, admitted.org_policy
        stable = (
            f"You are responding inside a Slack thread (channel '{channel_id}', thread "
            f"'{thread_ts}'). Before any actionable work call `update_status` with a concise "
            "present-continuous summary (e.g. 'Cloning repository', 'Running test suite') - "
            "Slack shows it as the status line under the thread. "
            "Each user message is prefixed '[Speaker: <name> (<@id>)]'; attribute statements "
            "to the right person. "
            "Your final answer is delivered to the person by the platform: just write it. Never "
            "use `slack_send_message` or `slack_send_dm` to reply in this conversation; use them "
            "only to reach another channel or person when asked. "
        )
        if is_dm:
            stable += "This is a private 1:1 conversation. "
        elif admitted.private:
            stable += (
                "This request is private: only the requester sees your work and your answer. "
                "Do not post in the channel unless they ask. "
            )
        else:
            stable += (
                "This is a shared team thread: keep answers clear, professional and concise, "
                "formatted for Slack. "
            )
        dynamic = ""
        thread_context = await self._load_thread_context(channel_id, thread_ts)
        if thread_context:
            dynamic = (
                "Slack Thread Context (chronological transcript, most recent last):\n"
                f"{thread_context}\n"
                "Use `slack_read_thread` for more thread history and "
                "`slack_read_channel_history` for preceding channel messages when needed."
            )
        if attachment_note:
            dynamic = f"{dynamic}\n{attachment_note}".strip()
        profile = None if is_dm else org_policy.profile_for(channel_id)
        if profile and profile.instructions:
            stable += f"House rules for this channel: {profile.instructions} "
        allowed = org_policy.allowed_tools
        if profile and profile.allowed_tools:
            allowed = (
                tuple(t for t in allowed if t in profile.allowed_tools)
                if allowed and "*" not in allowed
                else profile.allowed_tools
            )
        return TurnOverlay(
            stable_suffix=stable,
            dynamic=dynamic,
            enabled_toolsets=frozenset({"slack"}),
            attachments=attachments,
            default_agent_id=self._agent_for(org_policy, profile),
            tool_scope=lambda base: self._slack_whitelisted_tools(base=base, org_allowed=allowed),
            denied_tools=self._slack_tool_selection(allowed)[1],
        )

    def _agent_for(self, org_policy: SlackOrgPolicy, profile: ChannelProfile | None) -> str | None:
        """The channel's agent, else the routed or bot's agent, else the organization's."""
        return (
            (profile.agent_persona if profile else "") or org_policy.default_agent_persona or None
        )

    async def hibernate_inactive_sessions(self, timeout_seconds: int | None = None) -> list[str]:
        """Stop ephemeral containers for idle threads while retaining persistent volumes."""
        timeout = (
            timeout_seconds if timeout_seconds is not None else self.settings.auto_hibernate_seconds
        )
        now = time.time()
        hibernated: list[str] = []

        if not self.sandbox_driver:
            return hibernated

        for session_id, last_time in list(self._last_activity.items()):
            if now - last_time >= timeout:
                try:
                    await self.sandbox_driver.hibernate_sandbox(session_id)
                    hibernated.append(session_id)
                except Exception as err:
                    logger.warning("Failed to hibernate sandbox %s: %s", session_id, err)

        return hibernated

    async def is_connected(self) -> bool:
        """Check if Socket Mode client is actively connected."""
        if not self.handler or not getattr(self.handler, "client", None):
            return False
        try:
            client = self.handler.client
            if hasattr(client, "is_connected"):
                return await client.is_connected()
            return True
        except Exception:
            return False

    async def authenticate(self) -> None:
        """Confirm the bot token and learn the workspace identity (no socket needed).

        Every replica calls this so workspace binding and pairing work wherever a request
        lands, even on a replica that does not hold the Socket Mode connection.
        """
        try:
            auth_info = await self.app.client.auth_test()
            self.bot_user_id = auth_info.get("user_id")
            self.bot_user_name = auth_info.get("user")
            self.team_name = auth_info.get("team")
            self.team_id = auth_info.get("team_id")
            self._slack_api_ready = True
            logger.info(
                "Slack authentication confirmed for team '%s' (%s) as @%s (%s)",
                self.team_name,
                self.team_id,
                self.bot_user_name,
                self.bot_user_id,
            )
        except Exception as err:
            logger.warning("Slack auth_test check during startup warning: %s", err)

    async def start_socket_mode(self) -> None:
        """Establish persistent outbound WebSocket connection with Slack."""
        if not self.settings.slack_app_token:
            raise ValueError("SLACK_APP_TOKEN is required for Socket Mode")
        if not self.handler:
            self.handler = AsyncSocketModeHandler(
                app=self.app,
                app_token=self.settings.slack_app_token,
            )
        await self.authenticate()
        if self.settings.slack_health_check_seconds > 0:
            self._watchdog = asyncio.create_task(self._watch_connection())
        # slack-bolt AsyncSocketModeHandler.start_async method lacks type hints
        await self.handler.start_async()  # type: ignore[no-untyped-call]

    async def _watch_connection(self) -> None:
        """Reconnect a Socket Mode client that stopped without telling anyone.

        The client reconnects on its own after most failures, but a half-open connection can
        leave the bot deaf while every process looks healthy; two failed checks in a row
        (not one, so a reconnect in progress is left alone) force a fresh connection.
        """
        interval = self.settings.slack_health_check_seconds
        while True:
            await asyncio.sleep(interval)
            if await self.is_connected():
                self._health.consecutive_unhealthy_checks = 0
                continue
            self._health.consecutive_unhealthy_checks += 1
            if self._health.consecutive_unhealthy_checks < 2 or self.handler is None:
                continue
            logger.warning("Slack Socket Mode is down; reconnecting")
            try:
                # slack-bolt AsyncSocketModeHandler.connect_async method lacks type hints
                await self.handler.connect_async()  # type: ignore[no-untyped-call]
                self._health.reconnects += 1
                self._health.consecutive_unhealthy_checks = 0
            except Exception as err:
                logger.warning("Slack Socket Mode reconnect failed: %s", err)

    async def stop_socket_mode(self) -> None:
        """Cleanly close Socket Mode connection."""
        self._go_offline()
        watchdog, self._watchdog = self._watchdog, None
        if watchdog is not None:
            watchdog.cancel()
        if self.handler:
            handler, self.handler = self.handler, None
            # slack-bolt AsyncSocketModeHandler.close_async method lacks type hints
            await handler.close_async()  # type: ignore[no-untyped-call]


def _new_session_record(
    session_id: str,
    admitted: _Admitted,
    channel_id: str,
    thread_ts: str,
    slack_user_id: str | None,
) -> SessionRecord:
    title_source = admitted.title_text[:30]
    title = f"Slack DM: {title_source}" if admitted.is_dm else f"Slack: {title_source}"
    return SessionRecord(
        session_id=session_id,
        tenant_org_id=admitted.session_org_id,
        tenant_user_id=admitted.session_user_id,
        title=title,
        source=SessionSource.SLACK,
        container_image=admitted.sandbox_image or None,
        is_shared=not (admitted.is_dm or admitted.private),
        metadata={
            "slack_channel_id": channel_id,
            "slack_thread_ts": thread_ts,
            "slack_user_id": slack_user_id,
            "slack_is_dm": admitted.is_dm,
            "slack_private": admitted.private,
            # A placeholder: the AI titler names the session after the first answer.
            "title_source": "auto",
            # Slack tools stay available for every later turn of this session.
            "toolsets": ["slack"],
        },
    )


def _with_route(route: str) -> Callable[[SessionRecord], None]:
    """Remember the route chosen for a thread, so its follow-ups keep it (sticky routing)."""

    def remember(record: SessionRecord) -> None:
        known = record.metadata if isinstance(record.metadata, dict) else {}
        record.metadata = {**known, ROUTE_KEY: route}

    return remember
