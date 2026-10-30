"""Transport-agnostic turn preparation and invocation.

Both the web cockpit and the Slack assistant run turns against the same orchestrator, but
each used to re-implement the pre-turn setup (agent persona, model routing, turn limit,
tool scope). That duplication is what let the two paths drift (memory bound only on web,
Slack ignoring the bound agent, etc.). ``TurnService`` owns that setup once and returns a
``AgentBinding`` describing how the turn should run; transports only add their overlay and
render the event stream.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from fnmatch import fnmatch
from types import EllipsisType
from typing import Any

from agent_core.tool_loading import current_tool_names
from config_engine import Preset
from config_engine.cascading import RoutingPolicy, model_is_allowed
from config_engine.model_defaults import fallback_models_for

from api.auth.capabilities import Capability, member_has
from api.services.agent_access import AgentAccessDenied
from api.services.github_installations import installations_for_org
from api.services.model_catalogue import default_catalogue
from api.services.model_defaults import organization_providers
from api.services.openrouter_efforts import ReasoningSupport, map_effort, openrouter_efforts
from api.services.settings import (
    DeclaredSettings,
    effective_settings,
    resolve_setting,
    resolve_settings,
)
from api.services.tool_loading import tool_loading_for
from specifications.interfaces.agent import (
    DEFAULT_PERMISSION_MODE,
    GIT_PUBLISH_AS,
    GIT_SIGNING,
    HOOK_EVENTS,
    PERMISSION_MODES,
    AgentBinding,
    AgentEvent,
    AgentOrchestratorProtocol,
    GenerationParams,
    GitPolicy,
    HookSpec,
    ModelAttribution,
    ReasoningPolicy,
    SystemPrompt,
    TurnCallbacks,
    TurnPolicy,
)
from specifications.interfaces.llm import CredentialBinding

ResolveAgent = Callable[[Any, str, str, str | None], Awaitable[Any]]
ResolveRouting = Callable[..., Awaitable[tuple[Any, str, str]]]


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
    agent_mode: str | None = None,
) -> str:
    """The supervision level for a turn: the session's choice, else the agent's, else the preference.

    The same rule as the policy resolver applies to ``user_preferences.permission_mode`` with
    the session and the agent as presets (see :func:`permission_mode_for`); kept for callers
    that already hold the values. Bypass removes every gate, so it only applies where the
    member's role holds ``use_bypass_mode``; otherwise a request for it runs as ``ask`` (more
    supervision, never less).
    """
    chosen = preferred_mode
    if not locked:
        chosen = next(
            (str(mode) for mode in (session_mode, agent_mode) if mode in PERMISSION_MODES),
            preferred_mode,
        )
    return _supervised(chosen, allow_bypass)


def _supervised(mode: str, allow_bypass: bool) -> str:
    if mode not in PERMISSION_MODES:
        return DEFAULT_PERMISSION_MODE
    if mode == "bypass" and not allow_bypass:
        return "ask"
    return mode


def _agent_source(agent: Any) -> str:
    return f"agent:{agent.id}" if agent is not None else "agent"


def _presets(
    agent: Any, session: dict[str, Any], agent_values: dict[str, Any]
) -> tuple[Preset, ...]:
    """The session's own choice first, then the agent's (both checked by policy and lock)."""
    return (
        Preset("session", {k: v for k, v in session.items() if v not in (None, "")}),
        Preset(
            _agent_source(agent), {k: v for k, v in agent_values.items() if v not in (None, "")}
        ),
    )


async def permission_mode_for(
    db_mgr: Any,
    org_id: str,
    requester_id: str,
    agent: Any,
    session_mode: str | None,
) -> str:
    """The turn's permission mode through the policy resolver, then the bypass capability."""
    resolved = await resolve_setting(
        db_mgr,
        org_id,
        "user_preferences",
        "permission_mode",
        user_id=requester_id,
        presets=_presets(
            agent,
            {"permission_mode": session_mode if session_mode in PERMISSION_MODES else None},
            {"permission_mode": getattr(agent, "default_permission_mode", None)},
        ),
    )
    allow_bypass = await member_has(db_mgr, org_id, requester_id, Capability.USE_BYPASS_MODE)
    return _supervised(str(resolved.value or ""), allow_bypass)


async def step_limit_for(db_mgr: Any, org_id: str, agent: Any) -> int:
    """The agent's step limit, never above the organization's (a ceiling)."""
    own = getattr(agent, "max_turns", None)
    resolved = await resolve_setting(
        db_mgr,
        org_id,
        "general",
        "max_turns",
        presets=(Preset(_agent_source(agent), {"max_turns": own} if own is not None else {}),),
    )
    return int(resolved.value)


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


def _context_target(raw: object) -> int:
    """The organization's working context size; anything unusable means the whole window."""
    try:
        return max(0, int(str(raw)))
    except (TypeError, ValueError):
        return 0


def _provider_preferences(raw: object) -> tuple[tuple[str, dict[str, object]], ...]:
    """The organization's provider preferences, keeping only usable entries."""
    if not isinstance(raw, dict):
        return ()
    return tuple(
        (str(pattern).strip(), dict(prefs))
        for pattern, prefs in raw.items()
        if str(pattern).strip() and isinstance(prefs, dict) and prefs
    )


def _match_output_tokens(model: str, mapping: Any) -> int | None:
    """Find the max output tokens configured for a model (exact or wildcard)."""
    if not isinstance(mapping, dict):
        return None
    clean = model.lower()
    base = clean.split("/")[-1]
    # Exact match takes precedence over wildcard patterns.
    for key, val in mapping.items():
        k = str(key).lower()
        if k == clean or k == base:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    for pattern, val in mapping.items():
        pat = str(pattern).lower()
        if fnmatch(clean, pat) or ("/" not in pat and fnmatch(base, pat)):
            try:
                return int(val)
            except (ValueError, TypeError):
                continue
    return None


# ``models.temperature``'s registry default: a value other than it was chosen by someone.
PLATFORM_TEMPERATURE = 0.2


async def _fallbacks(
    db_mgr: Any, org_id: str, model: str | None, models: Mapping[str, Any], policy: RoutingPolicy
) -> tuple[str, ...]:
    """Fail-over models: the organization's list, else (``automatic_fallbacks``) the same tier
    through the other providers it holds a key for. Either obeys the same allowlist as the
    primary: a disallowed model must not become reachable through a provider error."""
    configured = [str(m) for m in models.get("fallback_models") or []]
    if not configured and model and models.get("automatic_fallbacks", True):
        configured = list(fallback_models_for(model, await organization_providers(db_mgr, org_id)))
    return tuple(
        m
        for m in configured
        if model_is_allowed(
            m, allowed_models=policy.allowed_models, forbidden_models=policy.forbidden_models
        )
    )


# Toolsets whose tools are hidden unless the session enables them, by the prefix of their tools.
_TOOLSET_PREFIXES = {"slack": "slack_"}


def toolsets_listed(tools: Sequence[str] | None) -> frozenset[str]:
    """The toolsets an agent's tool list asks for: an agent given Slack tools uses them in
    every session, not only in the sessions a Slack bot started."""
    return frozenset(
        toolset
        for toolset, prefix in _TOOLSET_PREFIXES.items()
        if any(str(tool).startswith(prefix) for tool in tools or ())
    )


def _context_ratios(models: Mapping[str, Any]) -> dict[str, float]:
    """``context_prune_percent`` / ``context_compact_percent`` as ratios, compaction kept above
    pruning (pruning first is what keeps most turns from needing a summary at all)."""
    prune = int(models.get("context_prune_percent") or 70) / 100
    compact = int(models.get("context_compact_percent") or 85) / 100
    return {
        "context_prune_ratio": prune,
        "context_compact_ratio": max(compact, min(0.98, prune + 0.05)),
    }


def _temperature(models: dict[str, Any], agent: Any, *, reasoning: bool) -> float | None:
    """The sampling temperature, or None for the provider's own: a reasoning model gets the
    provider's unless the agent or the organization chose another than the platform default
    (several reasoning APIs reject a temperature)."""
    value = float(models["temperature"])
    chosen = getattr(agent, "temperature", None) is not None or value != PLATFORM_TEMPERATURE
    return value if chosen or not reasoning else None


def _output_budget(model: str | None, agent: Any, models: dict[str, Any], spec: Any) -> int | None:
    """Completion tokens per call: the agent's own limit, else the model's ``output_tokens``
    entry, else the smaller of ``max_tokens`` and the model's own maximum output."""
    agent_tokens = getattr(agent, "max_tokens", None)
    if agent_tokens is not None:
        return int(agent_tokens)
    if (
        model
        and (configured := _match_output_tokens(model, models.get("output_tokens"))) is not None
    ):
        return configured
    cap = int(models["max_tokens"]) if models.get("max_tokens") else None
    own = int(spec.max_output_tokens) if spec is not None else None
    if cap is None or own is None:
        return cap or own
    return min(cap, own)


def _resolve_model_generation(
    model: str | None,
    agent: Any,
    models: dict[str, Any],
    fallbacks: tuple[str, ...],
    efforts: ReasoningSupport | None = None,
) -> GenerationParams:
    """Resolve generation params with output budget ceiling and reasoning policy."""
    # ``models`` is already resolved with the agent as a preset (``temperature``,
    # ``default_reasoning_effort``), so a locked organization value wins over the agent's.
    spec = default_catalogue.get_model_spec(model) if model else None
    temperature = _temperature(models, agent, reasoning=bool(spec and spec.supports_reasoning))
    max_tokens = _output_budget(model, agent, models, spec)

    # One reasoning control: the effort. The thinking budget follows from it below.
    thinking_budget: int | None = None
    step_limit = int(models.get("reasoning_step_limit_tokens") or 0)
    effort = str(models.get("default_reasoning_effort") or "") or None
    reasoning_off = effort == "none"
    if reasoning_off:
        # Only OpenRouter takes "none" as an effort; for any other provider, off is no effort
        # and no thinking budget, and the reasoning default below must not turn it back on.
        thinking_budget = None
        effort = effort if model and model.startswith("openrouter/") else None
    elif effort is not None:
        effort = map_effort(effort, efforts)
    reasoning_policy: ReasoningPolicy | None = None
    if spec and spec.supports_reasoning and not reasoning_off:
        if effort is None and thinking_budget is None:
            effort = "medium"
            if model and ("claude" in model.lower() or "anthropic" in model.lower()):
                thinking_budget = min(16_000, step_limit) if step_limit > 0 else 16_000
        if thinking_budget is not None and max_tokens is not None and thinking_budget >= max_tokens:
            thinking_budget = max(1024, max_tokens - 1024) if max_tokens > 1024 else None
        reasoning_policy = ReasoningPolicy(effort=effort or "medium", budget_tokens=thinking_budget)

    # When thinking_budget is explicitly set and step_limit is lower, protect the stream
    # from being severed prematurely by ensuring step_limit leaves headroom past budget_tokens
    if thinking_budget is not None and step_limit > 0 and step_limit <= thinking_budget:
        step_limit = thinking_budget + 2048

    return GenerationParams(
        temperature=temperature,
        max_tokens=max_tokens,
        thinking_budget_tokens=thinking_budget,
        reasoning_effort=effort,
        reasoning=reasoning_policy,
        reasoning_step_limit_tokens=step_limit,
        fallback_models=fallbacks,
        context_windows=_context_windows(models.get("context_windows")),
        context_target_tokens=_context_target(models.get("context_target_tokens")),
        provider_preferences=_provider_preferences(models.get("provider_preferences")),
        **_context_ratios(models),
    )


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
        default_model: str = "",
    ) -> None:
        self._resolve_agent = resolve_agent
        self._resolve_routing = resolve_routing
        self._default_model = default_model

    async def plan_turn(
        self,
        *,
        db_mgr: Any,
        org_id: str,
        user_id: str,
        agent_id: str | None,
        request_model: str | None = None,
        reasoning_effort: str | None = None,
        enabled_toolsets: frozenset[str] | None = None,
        global_default_model: str | None = None,
        session_permission_mode: str | None = None,
        requester_user_id: str | None = None,
        turn_id: str | None = None,
    ) -> AgentBinding:
        """Resolve how the turn should run: persona, model, turn limit, tool scope.

        ``user_id`` is the session's owner (the tenant of record); ``requester_user_id`` the
        person who sent the message. Agent access, member keys, member limits and the bypass
        capability are the requester's.
        """
        requester = requester_user_id or user_id
        agent = await self._resolve_agent(db_mgr, org_id, requester, agent_id)
        if agent_id and agent is None:
            raise AgentAccessDenied(agent_id, "")
        if agent is not None and getattr(agent, "enabled", True) is False:
            raise ValueError(
                f"Agent persona '{agent.name}' ({agent.id}) has been disabled for this organization."
            )
        router, model, reason = await self._resolve_routing(
            db_mgr=db_mgr,
            org_id=org_id,
            user_id=requester,
            agent_model=agent.model if agent else None,
            request_model=request_model,
            global_default_model=global_default_model or self._default_model,
        )
        policy = await self._resolve_policy(
            db_mgr,
            org_id,
            requester,
            agent,
            session_permission_mode,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        policy = replace(
            policy,
            attribution=ModelAttribution(
                requester_user_id=requester,
                agent_id=agent.id if agent else None,
                credential_bindings=tuple(
                    CredentialBinding(b.provider, b.credential_id)
                    for b in getattr(agent, "credential_bindings", None) or ()
                ),
                strict_binding=bool(getattr(agent, "strict_binding", False)),
                turn_id=turn_id,
            ),
        )
        return AgentBinding(
            id=agent.id if agent else "persona-general",
            name=agent.name if agent else "",
            system_prompt=agent.system_prompt if agent else None,
            whitelisted_tools=(
                current_tool_names(list(agent.whitelisted_tools))
                if agent and agent.whitelisted_tools
                else None
            ),
            model=model,
            max_turns=await step_limit_for(db_mgr, org_id, agent),
            enabled_toolsets=(enabled_toolsets or frozenset())
            | toolsets_listed(agent.whitelisted_tools if agent else None),
            model_router=router,
            routing_reason=reason,
            policy=policy,
            sandbox_id=getattr(agent, "sandbox_id", "") or "",
            knowledge_base_ids=tuple(getattr(agent, "knowledge_base_ids", None) or ()),
            skill_ids=tuple(getattr(agent, "skill_ids", None) or ()),
        )

    @staticmethod
    async def _git_policy(db_mgr: Any, org_id: str, user_id: str, agent: Any) -> GitPolicy:
        """Publishing identity and signing: ``publish_as`` (an organization default a member
        may change, ``bot`` only with ``publish_as_bot``) and the organization's ``signing``."""
        github = await effective_settings(db_mgr, org_id, "github", user_id)
        prefs = await effective_settings(db_mgr, org_id, "user_preferences", user_id)
        publish_as = str(github["publish_as"])
        if publish_as == "bot" and not await member_has(
            db_mgr, org_id, user_id, Capability.PUBLISH_AS_BOT
        ):
            resolved = await resolve_setting(db_mgr, org_id, "github", "publish_as")
            publish_as = str(resolved.value)
        signing = str(github["signing"])
        return GitPolicy(
            publish_as=publish_as if publish_as in GIT_PUBLISH_AS else "member",
            signing=signing if signing in GIT_SIGNING else "none",
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

    @staticmethod
    async def _resolve_policy(
        db_mgr: Any,
        org_id: str,
        user_id: str,
        agent: Any,
        session_permission_mode: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> TurnPolicy:
        """Generation parameters and git rules from the registry-backed org/member settings."""
        models_resolved = await resolve_settings(
            db_mgr,
            org_id,
            "models",
            presets=_presets(
                agent,
                {"default_reasoning_effort": reasoning_effort},
                {
                    "default_reasoning_effort": getattr(agent, "reasoning_effort", None),
                    "temperature": getattr(agent, "temperature", None),
                },
            ),
        )
        models = DeclaredSettings("models", models_resolved.effective)
        governance = await effective_settings(db_mgr, org_id, "governance")
        policy = RoutingPolicy.from_settings(
            {
                "allowed_models": models.get("allowed_models"),
                "forbidden_models": models.get("forbidden_models"),
            }
        )
        # Fail-over models obey the same allowlist as the primary: a disallowed model must
        # not become reachable through a provider error.
        fallbacks = await _fallbacks(db_mgr, org_id, model, models, policy)
        support = await openrouter_efforts.support_for(model) if model else None
        generation = _resolve_model_generation(model, agent, models, fallbacks, support)
        general = await effective_settings(db_mgr, org_id, "general")
        budgets = await effective_settings(db_mgr, org_id, "budgets")
        max_cost = float(budgets["max_turn_cost_usd"]) if budgets.get("max_turn_cost_usd") else None
        sandbox_profile = str(getattr(agent, "sandbox_profile", "") or "standard")
        return TurnPolicy(
            generation=generation,
            git=await TurnService._git_policy(db_mgr, org_id, user_id, agent),
            hooks=parse_hooks(governance.get("hooks")),
            permission_mode=await permission_mode_for(
                db_mgr, org_id, user_id, agent, session_permission_mode
            ),
            tool_loading=tool_loading_for(agent),
            output_offload_threshold=int(general.get("output_offload_threshold", 6000)),
            max_turn_cost_usd=max_cost,
            sandbox_profile=sandbox_profile,
            plan_critic=bool(general["plan_critic"]),
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
        synthetic_prompt: bool = False,
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
            synthetic_prompt=synthetic_prompt,
        ):
            yield event
