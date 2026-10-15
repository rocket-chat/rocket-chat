"""Transport-agnostic turn preparation and invocation.

Both the web cockpit and the Slack assistant run turns against the same orchestrator, but
each used to re-implement the pre-turn setup (agent persona, model routing, turn limit,
tool scope). That duplication is what let the two paths drift (memory bound only on web,
Slack ignoring the bound agent, etc.). ``TurnService`` owns that setup once and returns a
``AgentBinding`` describing how the turn should run; transports only add their overlay and
render the event stream.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from types import EllipsisType
from typing import Any

from config_engine.cascading import RoutingPolicy, model_is_allowed

from api.services.github_installations import installations_for_org
from api.services.settings import effective_settings, resolve_settings
from specifications.interfaces.agent import (
    DEFAULT_PERMISSION_MODE,
    HOOK_EVENTS,
    PERMISSION_MODES,
    AgentBinding,
    AgentEvent,
    AgentOrchestratorProtocol,
    GenerationParams,
    GitPolicy,
    HookSpec,
    SystemPrompt,
    TurnCallbacks,
    TurnPolicy,
)

ResolveAgent = Callable[[Any, str, str, str | None], Awaitable[Any]]
ResolveRouting = Callable[..., Awaitable[tuple[Any, str, str]]]
ResolveMaxTurns = Callable[..., Awaitable[int]]


def parse_hooks(raw: Any) -> tuple[HookSpec, ...]:
    """Organization hooks from settings; malformed entries are dropped, never fatal."""
    if not isinstance(raw, dict):
        return ()
    hooks: list[HookSpec] = []
    for event in HOOK_EVENTS:
        entries = raw.get(event)
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            match, command = str(entry.get("match") or "*"), str(entry.get("command") or "").strip()
            timeout = entry.get("timeout_seconds")
            if command:
                hooks.append(
                    HookSpec(
                        event=event,
                        match=match,
                        command=command,
                        timeout_seconds=min(max(int(timeout), 1), 300)
                        if isinstance(timeout, int)
                        else 30,
                    )
                )
    return tuple(hooks)


def resolve_permission_mode(
    session_mode: str | None,
    preferred_mode: str,
    *,
    locked: bool,
    allow_bypass: bool,
) -> str:
    """The supervision level for a turn: the session's choice, else the effective preference.

    The preference already cascades organization default -> member override. When the
    organization locked it, neither a member nor a session may choose another mode. Bypass
    removes every gate, so it only applies where the organization allows it; otherwise a
    request for it is honoured as ``ask`` (more supervision, never less).
    """
    chosen = preferred_mode
    if not locked and session_mode in PERMISSION_MODES:
        chosen = str(session_mode)
    if chosen not in PERMISSION_MODES:
        return DEFAULT_PERMISSION_MODE
    if chosen == "bypass" and not allow_bypass:
        return "ask"
    return chosen


def _context_windows(raw: object) -> tuple[tuple[str, int], ...]:
    """The organization's context-window sizes, keeping only usable entries."""
    if not isinstance(raw, dict):
        return ()
    windows: list[tuple[str, int]] = []
    for pattern, size in raw.items():
        try:
            tokens = int(size)
        except (TypeError, ValueError):
            continue
        if str(pattern).strip() and tokens > 0:
            windows.append((str(pattern).strip(), tokens))
    return tuple(windows)


class TurnService:
    """Resolves the turn plan for a session and invokes the orchestrator.

    The resolver callables are injected so this module has no dependency on the HTTP layer
    (``routes.py``) that defines them; the same instance is shared by the web handler and
    the Slack assistant (injected via ``main.py``).
    """

    def __init__(
        self,
        *,
        resolve_agent: ResolveAgent,
        resolve_routing: ResolveRouting,
        resolve_max_turns: ResolveMaxTurns,
        default_model: str = "",
    ) -> None:
        self._resolve_agent = resolve_agent
        self._resolve_routing = resolve_routing
        self._resolve_max_turns = resolve_max_turns
        self._default_model = default_model

    async def plan_turn(
        self,
        *,
        db_mgr: Any,
        org_id: str,
        user_id: str,
        agent_id: str | None,
        request_model: str | None = None,
        enabled_toolsets: frozenset[str] | None = None,
        global_default_model: str | None = None,
        session_permission_mode: str | None = None,
    ) -> AgentBinding:
        """Resolve how the turn should run: persona, model, turn limit, tool scope."""
        agent = await self._resolve_agent(db_mgr, org_id, user_id, agent_id)
        router, model, reason = await self._resolve_routing(
            db_mgr=db_mgr,
            org_id=org_id,
            user_id=user_id,
            agent_model=agent.model if agent else None,
            request_model=request_model,
            global_default_model=global_default_model or self._default_model,
        )
        max_turns = await self._resolve_max_turns(agent=agent, org_id=org_id, db_mgr=db_mgr)
        policy = await self._resolve_policy(db_mgr, org_id, user_id, agent, session_permission_mode)
        return AgentBinding(
            id=agent.id if agent else "persona-general",
            name=agent.name if agent else "",
            system_prompt=agent.system_prompt if agent else None,
            whitelisted_tools=(
                list(agent.whitelisted_tools) if agent and agent.whitelisted_tools else None
            ),
            model=model,
            max_turns=max_turns,
            enabled_toolsets=enabled_toolsets or frozenset(),
            model_router=router,
            routing_reason=reason,
            policy=policy,
            sandbox_image=getattr(agent, "sandbox_image", "") or "",
            cpu_limit=str(getattr(agent, "cpu_limit", "") or ""),
            memory_limit=str(getattr(agent, "memory_limit", "") or ""),
            knowledge_base_ids=tuple(getattr(agent, "knowledge_base_ids", None) or ()),
            skill_ids=tuple(getattr(agent, "skill_ids", None) or ()),
        )

    @staticmethod
    async def _resolve_policy(
        db_mgr: Any,
        org_id: str,
        user_id: str,
        agent: Any,
        session_permission_mode: str | None = None,
    ) -> TurnPolicy:
        """Generation parameters and git rules from the registry-backed org/member settings."""
        models = await effective_settings(db_mgr, org_id, "models")
        github = await effective_settings(db_mgr, org_id, "github")
        governance = await effective_settings(db_mgr, org_id, "governance")
        resolved_prefs = await resolve_settings(db_mgr, org_id, "user_preferences", user_id)
        prefs = resolved_prefs.effective

        policy = RoutingPolicy.from_settings(
            {
                "allowed_models": models.get("allowed_models"),
                "forbidden_models": models.get("forbidden_models"),
            }
        )
        # Fail-over models obey the same allowlist as the primary: a disallowed model must
        # not become reachable through a provider error.
        fallbacks = tuple(
            model
            for model in models.get("fallback_models") or []
            if model_is_allowed(
                model,
                allowed_models=policy.allowed_models,
                forbidden_models=policy.forbidden_models,
            )
        )
        agent_temperature = getattr(agent, "temperature", None)
        generation = GenerationParams(
            temperature=(
                float(agent_temperature)
                if agent_temperature is not None
                else float(models["temperature"])
            ),
            max_tokens=int(models["max_tokens"]) if models.get("max_tokens") else None,
            thinking_budget_tokens=getattr(agent, "thinking_budget_tokens", None),
            reasoning_effort=getattr(agent, "reasoning_effort", None),
            fallback_models=fallbacks,
            context_windows=_context_windows(models.get("context_windows")),
        )
        git = GitPolicy(
            authorship=str(github["commit_authorship_policy"]),
            pr_creation=str(github["pr_creation_policy"]),
            signing_mode=str(github["commit_signing_mode"]),
            default_branch=str(github["default_branch"]),
            bot_name=str(github["bot_author_name"]),
            bot_email=str(github["bot_author_email"]),
            # The member's own override only; the linked GitHub account or profile fills the rest
            # when the turn's git credentials are resolved (see ``commit_identity``).
            user_name=str(prefs.get("git_author_name") or ""),
            user_email=str(prefs.get("git_author_email") or ""),
            app_installations=tuple(
                (i.installation_id, i.account_login)
                for i in await installations_for_org(db_mgr, org_id)
            ),
        )
        return TurnPolicy(
            generation=generation,
            git=git,
            hooks=parse_hooks(governance.get("hooks")),
            permission_mode=resolve_permission_mode(
                session_permission_mode,
                str(prefs.get("permission_mode") or ""),
                locked="permission_mode" in resolved_prefs.locked_keys,
                allow_bypass=bool(governance.get("allow_bypass_permissions")),
            ),
        )

    async def stream_turn(
        self,
        orchestrator: AgentOrchestratorProtocol,
        plan: AgentBinding,
        session_id: str,
        prompt: str,
        *,
        system_prompt_parts: SystemPrompt,
        tenant_org_id: str,
        tenant_user_id: str,
        attachments: list[Any] | None = None,
        zero_retention: bool = False,
        region: str = "",
        enabled_toolsets: frozenset[str] | None = None,
        callbacks: TurnCallbacks | None = None,
        policy: TurnPolicy | None = None,
        whitelisted_tools: list[str] | EllipsisType | None = ...,
        denied_tools: frozenset[str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Invoke the orchestrator with the resolved plan; yields agent events.

        This is the single invocation contract every transport uses, so model/turn/toolset
        settings and governance flags are applied identically regardless of transport.
        """
        toolsets = enabled_toolsets if enabled_toolsets is not None else plan.enabled_toolsets
        async for event in orchestrator.process_user_turn(
            session_id,
            prompt,
            model=plan.model,
            system_prompt_parts=system_prompt_parts,
            whitelisted_tools=(
                plan.whitelisted_tools
                if isinstance(whitelisted_tools, EllipsisType)
                else whitelisted_tools
            ),
            denied_tools=denied_tools,
            tenant_org_id=tenant_org_id,
            tenant_user_id=tenant_user_id,
            max_turns=plan.max_turns,
            attachments=attachments,
            model_router=plan.model_router,
            zero_retention=zero_retention,
            region=region,
            enabled_toolsets=toolsets,
            callbacks=callbacks,
            policy=policy if policy is not None else plan.policy,
        ):
            yield event
