"""Turn limits, model routing and agent persona resolution."""

import logging
import os
from typing import Any

from config_engine.cascading import (
    ModelRouter,
    RoutingPolicy,
    resolve_model_precedence,
)

from api.models import (
    AgentPersonaModel,
)
from api.services.personas import (
    get_persona,
)
from api.services.settings import (
    effective_settings,
    load_org_config,
)

logger = logging.getLogger(__name__)


async def _resolve_effective_max_turns(
    agent: AgentPersonaModel | None,
    org_id: str,
    db_mgr: Any = None,
) -> int:
    """Resolve turn limit cascading: Agent persona -> Org policy -> Env var -> Default (30)."""
    if agent and agent.max_turns is not None:
        return int(agent.max_turns)

    org_general, _ = await load_org_config(db_mgr, org_id, "general")
    configured = org_general.get("max_turns")
    if configured is not None:
        try:
            return int(configured)
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid general.max_turns=%r for org %s", configured, org_id)

    env_max = (
        os.getenv("AGENT_MAX_TURNS") or os.getenv("ROCKET_MAX_TURNS") or os.getenv("MAX_TURNS")
    )
    if env_max and env_max.isdigit():
        return int(env_max)

    return 30


async def _resolve_turn_routing(
    *,
    db_mgr: Any,
    org_id: str,
    user_id: str,
    agent_model: str | None,
    request_model: str | None,
    global_default_model: str,
) -> tuple[ModelRouter, str, str]:
    """Resolve the effective model and routing policy for one turn.

    Returns ``(router, effective_model, reason)``. The effective model follows the
    documented precedence and is validated against the org allowlist; the router then
    maps each role of work to a model according to the routing mode.
    """
    routing_org, _ = await load_org_config(db_mgr, org_id, "routing")
    models_org, _ = await load_org_config(db_mgr, org_id, "models")
    prefs = await effective_settings(db_mgr, org_id, "user_preferences", user_id)

    # Allow/deny lists belong to the `models` domain; `routing` only picks the mode/models.
    routing_config = {
        "mode": routing_org.get("mode"),
        "fast_model": routing_org.get("fast_model"),
        "economy_model": routing_org.get("economy_model"),
        "allowed_models": models_org.get("allowed_models"),
        "forbidden_models": models_org.get("forbidden_models"),
    }

    # Build the policy first: it normalizes comma-separated org settings into pattern
    # tuples, which the precedence resolver and the router both enforce.
    policy = RoutingPolicy.from_settings(routing_config)
    effective_model, reason = resolve_model_precedence(
        agent_model=agent_model or None,
        request_model=request_model,
        user_preferred_model=prefs.get("preferred_model"),
        org_default_model=models_org.get("default_model"),
        global_default_model=global_default_model,
        allowed_models=policy.allowed_models,
        forbidden_models=policy.forbidden_models,
    )
    return ModelRouter(frontier_model=effective_model, policy=policy), effective_model, reason


async def resolve_agent_persona(
    db_mgr: Any,
    org_id: str,
    user_id: str,
    agent_id: str | None,
) -> AgentPersonaModel | None:
    """Resolve the agent persona a session is bound to, as visible to this member.

    Shared by the web turn handler and the Slack assistant so both behave like the same
    bound agent. Always read from the store: there is no process-wide cache, so a persona
    cannot leak across organizations or go stale after an edit on another replica.
    """
    try:
        return await get_persona(db_mgr, org_id, user_id, agent_id)
    except Exception as err:
        logger.warning("Failed to resolve agent persona %s: %s", agent_id, err)
        return None
