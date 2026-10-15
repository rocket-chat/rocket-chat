"""Running one agent turn end to end, as a short sequence of named phases.

``TurnRunner.run`` reads top to bottom: prepare (session, trace, governance, plan), execute
(credentials, prompt, tools, stream), record the answer, offer a follow-up, and always clean up.
Each phase is a small method; event handling, the system prompt, callbacks and finalisation
live in their own modules.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from git_engine.engine import GitEngine

from api.db.store import _auto_provision_tenants_enabled
from api.services.audit import AuditService, GovernancePolicy
from api.services.budgets import BudgetService
from api.services.git_credentials import (
    GitCredentialBundle,
    inject_git_credentials_into_sandbox,
    resolve_session_git_credentials,
)
from api.services.git_gateway_env import github_api_url_of
from api.services.git_signing import (
    SIGNED_MODE,
    effective_signing_policy,
    member_signing_for_turn,
)
from api.services.mcp_turn import _mcp_tools_for_turn
from api.services.redaction import RedactionPolicy, Redactor
from api.services.sandbox_catalogue import SandboxCatalogue
from api.services.session_support import (
    _new_message_id,
    _repo_slug,
    _session_toolsets,
    active_repository_of,
)
from api.services.settings import effective_settings, load_org_config
from api.services.title import TITLE_SOURCE_AUTO
from api.services.turn_trace import (
    TurnTraceAccumulator,
    begin_turn_trace,
    user_turn_index,
)
from api.telemetry import traced_turn
from api.turns.callbacks import build_turn_callbacks
from api.turns.context import TurnContext, TurnRequest, TurnState
from api.turns.events import TurnEventHandlers
from api.turns.finalise import housekeeping, persist_trace, publish_followup, record_answer
from api.turns.prompt import build_system_prompt
from api.turns.provisioning import _expand_slash_command
from api.turns.shared import _turn_service
from specifications.interfaces.agent import DEFAULT_MAX_TURNS, AgentBinding, TurnOverlay
from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent
from specifications.interfaces.llm import ChatMessage, ModelScope
from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol

logger = logging.getLogger(__name__)

_PLACEHOLDER_TITLES = ("new session", "new chat", "mission", "")


class TurnRunner:
    def __init__(self, request: TurnRequest) -> None:
        self.req = request
        self.state = TurnState()
        self._org_id: str | None = None

    async def run(self) -> None:
        try:
            ctx = await self._prepare()
            await self._execute(ctx)
            answer = await record_answer(ctx)
            await publish_followup(ctx, answer)
        except Exception as err:
            await self._fail(err)
        finally:
            await self._cleanup()

    # -- phase 1: everything resolved before the model is called ---------------------------

    async def _prepare(self) -> TurnContext:
        req, state = self.req, self.state
        session = await self._load_session()
        self._org_id = session.tenant_org_id
        await self._record_user_message(session)

        trace = TurnTraceAccumulator(
            session_id=req.session_id,
            org_id=session.tenant_org_id,
            turn_index=user_turn_index(session.conversation_history),
            resume_attempt=req.resume_attempt,
        )
        state.trace = trace
        # Durable running marker before any work: if the process dies mid-turn the startup
        # recovery pass can surface the turn as interrupted (G1) instead of it vanishing with
        # the socket.
        await begin_turn_trace(req.db_mgr, trace)

        # Governance: the redaction pipeline scrubs tool output before it is
        # stored in the trace/history, the audit trail records every sensitive action, and
        # the per-org approval policy decides which risk levels need a human gate.
        settings, _ = await load_org_config(req.db_mgr, session.tenant_org_id, "governance")
        governance = GovernancePolicy.from_settings(settings)

        binding, model, reason = await self._plan(session, governance, trace)
        state.synthesis_turns = binding.max_turns or DEFAULT_MAX_TURNS
        return TurnContext(
            req=req,
            state=state,
            session=session,
            trace=trace,
            binding=binding,
            governance=governance,
            redactor=Redactor(RedactionPolicy.from_settings(settings)),
            audit=AuditService(req.db_mgr),
            effective_model=model,
            routing_reason=reason,
            effective_max_turns=binding.max_turns or DEFAULT_MAX_TURNS,
        )

    async def _load_session(self) -> SessionRecord:
        req = self.req
        session = await req.store.get_session(req.session_id)
        if session:
            return session
        if not _auto_provision_tenants_enabled():
            # Every entry point creates its session before the turn. Inventing one here would
            # hand it to a development identity in another organization.
            raise LookupError(f"Session {req.session_id} does not exist")
        clean_prompt = " ".join(req.prompt.strip().split())
        title = clean_prompt[:45].strip() + ("..." if len(clean_prompt) > 45 else "")
        session = SessionRecord(
            session_id=req.session_id,
            tenant_org_id="default_org",
            tenant_user_id="dev_user",
            title=title or "New Session",
            sandbox_status=SandboxStatus.NON_EXISTENT,
        )
        await req.store.create_session(session)
        return session

    async def _record_user_message(self, session: SessionRecord) -> None:
        req = self.req
        # A resumed turn continues the user's last instruction: the notice in `prompt` is for
        # the model only and must not appear as something the user said.
        if req.resume_attempt or (
            session.conversation_history and session.conversation_history[-1].content == req.prompt
        ):
            return
        author = req.author_user_id or session.tenant_user_id
        message = ChatMessage(role="user", content=req.prompt, author_user_id=author)
        saved = await req.store.mutate_session(
            session.session_id,
            session.tenant_org_id,
            lambda record: record.conversation_history.append(message),
        )
        # The in-memory store hands out the one shared record, which now has the message; a
        # database store returns a separate copy, so this turn's own snapshot needs it too.
        if saved is not session:
            session.conversation_history.append(message)
        # Multi-participant prompt propagation: the author renders the
        # instruction optimistically, so every other participant learns what was asked only
        # from this event. An identical trailing prompt is not re-appended, and must not be
        # re-broadcast either.
        await self._publish(
            EventType.USER_MESSAGE,
            {
                "message_id": req.message_id or _new_message_id(),
                "role": "user",
                "content": req.prompt,
                "author_user_id": author,
            },
        )

    async def _plan(
        self, session: SessionRecord, governance: GovernancePolicy, trace: TurnTraceAccumulator
    ) -> tuple[AgentBinding, str, str]:
        """Persona, model, limits and tool scope, through the shared TurnService.

        Model precedence: agent > this turn's selection > user pref > org policy >
        platform default, allowlist-validated, then a per-role router on top.
        """
        req, state = self.req, self.state
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        overlay = req.overlay
        binding = await _turn_service.plan_turn(
            db_mgr=req.db_mgr,
            org_id=session.tenant_org_id,
            user_id=session.tenant_user_id,
            agent_id=metadata.get("agent_id") or (overlay.default_agent_id if overlay else None),
            request_model=req.model,
            enabled_toolsets=self._toolsets(session),
            global_default_model=req.orchestrator.default_model,
            session_permission_mode=metadata.get("permission_mode"),
        )
        binding = await self._with_sandbox_capabilities(binding, session, metadata)
        model = binding.model or str(req.orchestrator.default_model)
        router = binding.model_router
        state.effective_model, state.router = model, router
        # Model data governance: the org can restrict which providers may see its
        # data. This is the server-side enforcement point - the model never chooses its own
        # provider.
        if not governance.model_data.provider_allowed(model):
            raise ValueError(
                f"Model provider for '{model}' is not permitted by the "
                "organization data-governance policy."
            )
        trace.set_routing(
            effective_model=model,
            mode=router.mode if router is not None else "frontier",
            role_models={
                role: router.model_for(role) if router is not None else model
                for role in ("react", "suggest", "summarize")
            },
        )
        return binding, model, binding.routing_reason or "default"

    async def _with_sandbox_capabilities(
        self, binding: AgentBinding, session: SessionRecord, metadata: dict[str, Any]
    ) -> AgentBinding:
        """Tell the registry what this session's sandbox can do, so unsupported tools are hidden."""
        catalogue = SandboxCatalogue(self.req.db_mgr)
        entry = await catalogue.resolve(
            session.tenant_org_id,
            sandbox_id=metadata.get("sandbox_id"),
            image=None
            if metadata.get("sandbox_id")
            else session.container_image or binding.sandbox_image or None,
        )
        capabilities = entry.capability_set if entry is not None else frozenset[str]()
        return replace(binding, policy=replace(binding.policy, sandbox_capabilities=capabilities))

    def _toolsets(self, session: SessionRecord) -> frozenset[str]:
        overlay = self.req.overlay
        return _session_toolsets(session) | (overlay.enabled_toolsets if overlay else frozenset())

    # -- phase 2: credentials, prompt, tools and the model stream --------------------------

    async def _execute(self, ctx: TurnContext) -> None:
        req, session = self.req, ctx.session
        await self._auto_title(ctx)
        await self._publish(
            EventType.STATUS_CHANGED, {"status": "running", "message": "Analyzing instruction..."}
        )

        member_prefs = await effective_settings(
            req.db_mgr, session.tenant_org_id, "user_preferences", session.tenant_user_id
        )
        git_bundle = await self._resolve_git(ctx, member_prefs)
        system_prompt = await build_system_prompt(ctx, git_bundle, member_prefs)
        self.state.mcp_tools = await _mcp_tools_for_turn(
            req.db_mgr, session.tenant_org_id, session.tenant_user_id
        )
        await self._warn_about_budget(ctx)
        attachments = await self._resolve_attachments(ctx)
        model_prompt = await _expand_slash_command(
            req.prompt,
            req.db_mgr,
            session.tenant_org_id,
            session.tenant_user_id,
            ctx.binding.skill_ids,
        )

        overlay = req.overlay
        handlers = TurnEventHandlers(ctx)
        async for event in _turn_service.stream_turn(
            req.orchestrator,
            ctx.binding,
            req.session_id,
            model_prompt,
            system_prompt_parts=system_prompt,
            tenant_org_id=session.tenant_org_id,
            tenant_user_id=session.tenant_user_id,
            attachments=attachments,
            # Model data governance: the gateway applies zero-retention and
            # regional routing for every request this turn (and its subagents) makes.
            zero_retention=ctx.governance.model_data.zero_retention,
            region=ctx.governance.model_data.region,
            # Session-enabled toolsets (e.g. "slack") make their tools available for the whole
            # session, on every turn - web included.
            enabled_toolsets=self._toolsets(session),
            policy=self._turn_policy(ctx, git_bundle),
            whitelisted_tools=(
                overlay.tool_scope(_restriction(ctx.binding.whitelisted_tools))
                if overlay is not None and overlay.tool_scope is not None
                else ...
            ),
            callbacks=await build_turn_callbacks(ctx, git_bundle, self.state.mcp_tools),
        ):
            await handlers.handle(event)

    async def _auto_title(self, ctx: TurnContext) -> None:
        """Silently name a default-titled session after its first message."""
        session = ctx.session
        is_default = (
            not session.title
            or session.title.startswith("Mission ")
            or session.title.strip().lower() in _PLACEHOLDER_TITLES
        )
        if not is_default or len(session.conversation_history) > 1:
            return
        words = self.req.prompt.strip().replace("\n", " ").split()
        candidates = [w for w in words if len(w) > 1 and not w.startswith("/")][:5]
        if not candidates:
            return
        title = " ".join(candidates)
        title = title[0].upper() + title[1:]
        if len(title) > 35:
            title = title[:32] + "..."
        session.title = title

        def set_placeholder_title(record: SessionRecord) -> None:
            record.title = title
            # A placeholder lets the AI titler replace it later, while a user rename
            # (title_source="user") is never overwritten.
            metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
            metadata["title_source"] = TITLE_SOURCE_AUTO
            record.metadata = metadata

        await self.req.store.mutate_session(
            session.session_id, session.tenant_org_id, set_placeholder_title
        )
        await self._publish(
            EventType.SESSION_UPDATED, {"title": title, "session_id": self.req.session_id}
        )

    async def _resolve_git(
        self, ctx: TurnContext, member_prefs: dict[str, Any]
    ) -> GitCredentialBundle:
        req, session = self.req, ctx.session
        git_bundle = await resolve_session_git_credentials(
            user_id=session.tenant_user_id,
            org_id=session.tenant_org_id,
            git_engine=req.git_engine,
            db_mgr=req.db_mgr,
            user_overrides=member_prefs,
            repo=_repo_slug(session.git_repo),
        )
        if ctx.binding.policy.git.signing_mode == SIGNED_MODE:
            signing = await member_signing_for_turn(
                req.db_mgr, session.tenant_org_id, session.tenant_user_id, member_prefs
            )
            if signing is not None:
                git_bundle = replace(git_bundle, signing=signing, author_email=signing.commit_email)
        if git_bundle.warning:
            await self._publish(
                EventType.STATUS_CHANGED, {"status": "warning", "message": git_bundle.warning}
            )
        # Renew the live sandbox's git setup at the start of every turn: the gateway capability
        # lasts an hour and a sandbox outlives many turns.
        # Best-effort: a refresh failure must never break the turn.
        if req.driver is not None and session.sandbox_status == SandboxStatus.RUNNING:
            await inject_git_credentials_into_sandbox(
                req.driver,
                req.session_id,
                git_bundle,
                org_id=session.tenant_org_id,
                user_id=session.tenant_user_id,
                api_url=github_api_url_of(req.git_engine),
            )
        return git_bundle

    @staticmethod
    def _turn_policy(ctx: TurnContext, git_bundle: GitCredentialBundle) -> Any:
        """The organization's policy completed with the member's git identity and token."""
        git = ctx.binding.policy.git
        completed = replace(
            git,
            user_name=git.user_name or git_bundle.author_name,
            user_email=git.user_email or git_bundle.author_email,
            user_token=git_bundle.user_token,
        )
        return replace(
            ctx.binding.policy,
            git=effective_signing_policy(completed, git_bundle.signing),
            active_repository=active_repository_of(ctx.session),
        )

    async def _warn_about_budget(self, ctx: TurnContext) -> None:
        """Tell the member early when a spending cap is close, instead of refusing mid-task.

        The cap itself is enforced by the gateway on every model call.
        """
        guard = getattr(self.req.orchestrator.gateway, "budget_guard", None)
        if not isinstance(guard, BudgetService):
            return
        try:
            status = await guard.status(
                ModelScope(
                    org_id=ctx.session.tenant_org_id,
                    user_id=ctx.session.tenant_user_id,
                    session_id=self.req.session_id,
                )
            )
            warnings = status.warnings()
        except Exception:
            logger.exception("Could not evaluate budget warnings")
            return
        for warning in warnings:
            await self._publish(
                EventType.STATUS_CHANGED, {"status": "budget_warning", "message": f"⚠ {warning}"}
            )

    async def _resolve_attachments(self, ctx: TurnContext) -> list[Any]:
        """Attachments belong to this turn only: the orchestrator never carries them forward."""
        req = self.req
        refs: list[Any] = []
        if req.attachment_service is not None and req.attachment_ids:
            refs = await req.attachment_service.resolve_refs(
                req.session_id, ctx.session.tenant_org_id, req.attachment_ids
            )
        if req.overlay is not None:
            refs = [*refs, *req.overlay.attachments]
        ctx.trace.record_attachments(refs)
        return refs

    # -- failure and cleanup ---------------------------------------------------------------

    async def _fail(self, err: Exception) -> None:
        req = self.req
        self.state.turn_error = err
        logger.exception("Error executing turn in session %s", req.session_id)
        await self._publish(EventType.ERROR, {"error": str(err)})
        await self._publish(
            EventType.STATUS_CHANGED,
            {"status": "error", "message": f"Mission failed: {err!s}", "final": True},
        )
        try:
            failure = ChatMessage(role="assistant", content=f"⚠️ **Mission Error**: {err!s}")
            if self._org_id is not None:
                await req.store.mutate_session(
                    req.session_id,
                    self._org_id,
                    lambda record: record.conversation_history.append(failure),
                )
        except Exception:
            pass

    async def _cleanup(self) -> None:
        if self.state.mcp_tools is not None:
            await self.state.mcp_tools.aclose()
        await persist_trace(self.state, self.req.db_mgr)
        await housekeeping(self.req, self.state)

    async def _publish(self, event_type: EventType, payload: dict[str, Any]) -> None:
        await self.req.event_bus.publish(
            SessionEvent(session_id=self.req.session_id, event_type=event_type, payload=payload)
        )


@traced_turn
async def execute_turn_background(
    session_id: str,
    prompt: str,
    orchestrator: AsyncReActOrchestrator,
    event_bus: EventBusProtocol,
    driver: SandboxDriverProtocol | None,
    registry: ToolRegistry | None,
    store: SessionStoreProtocol,
    model: str | None = None,
    git_engine: GitEngine | None = None,
    db_mgr: Any = None,
    message_id: str | None = None,
    author_user_id: str | None = None,
    attachment_service: Any = None,
    attachment_ids: list[str] | None = None,
    overlay: TurnOverlay | None = None,
    resume_attempt: int = 0,
) -> None:
    """Run one turn of ``session_id``; the entry point web, Slack, webhooks and missions share."""
    await TurnRunner(
        TurnRequest(
            session_id=session_id,
            prompt=prompt,
            orchestrator=orchestrator,
            event_bus=event_bus,
            driver=driver,
            registry=registry,
            store=store,
            model=model,
            git_engine=git_engine,
            db_mgr=db_mgr,
            message_id=message_id,
            author_user_id=author_user_id,
            attachment_service=attachment_service,
            attachment_ids=attachment_ids,
            overlay=overlay,
            resume_attempt=resume_attempt,
        )
    ).run()


def _restriction(whitelist: list[str] | None) -> list[str] | None:
    """An agent's tool list as ``TurnOverlay.tool_scope`` expects it: ``None`` when unrestricted."""
    return None if whitelist is None or "*" in whitelist else whitelist
