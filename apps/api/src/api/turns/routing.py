"""Turn limits, model routing and agent persona resolution."""

import logging
from dataclasses import replace
from typing import Any

from config_engine.cascading import (
    ModelRouter,
    RoutingPolicy,
    resolve_model_precedence,
)
from config_engine.model_defaults import tier_alias

from api.models import (
    AgentPersonaModel,
)
from api.services.agent_access import AgentAccessDenied
from api.services.model_defaults import model_for_tier, organization_default_model
from api.services.personas import (
    get_persona,
)
from api.services.settings import effective_settings
from api.services.turn_service import step_limit_for

logger = logging.getLogger(__name__)


async def _resolve_effective_max_turns(
    agent: AgentPersonaModel | None,
    org_id: str,
    db_mgr: Any = None,
) -> int:
    """The agent's own limit, never above the organization's; else the organization's."""
    return await step_limit_for(db_mgr, org_id, agent)


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
    models = await effective_settings(db_mgr, org_id, "models")
    prefs = await effective_settings(db_mgr, org_id, "user_preferences", user_id)

    routing_config = {
        "routing_mode": models.get("routing_mode"),
        "allowed_models": models.get("allowed_models"),
        "forbidden_models": models.get("forbidden_models"),
    }

    # Build the policy first: it normalizes comma-separated org settings into pattern
    # tuples, which the precedence resolver and the router both enforce.
    policy = RoutingPolicy.from_settings(routing_config)
    candidates = {
        "request_model": request_model,
        "user_preferred_model": prefs.get("preferred_model"),
        "org_default_model": models.get("default_model"),
        "global_default_model": await organization_default_model(
            db_mgr, org_id, models, global_default_model
        ),
        "allowed_models": policy.allowed_models,
        "forbidden_models": policy.forbidden_models,
    }
    if tier := tier_alias(agent_model):
        # An agent set to a tier (``tier:frontier``) takes that tier of the model the turn
        # would use without it, so it stays on a provider the organization has a key for.
        base_model, _ = resolve_model_precedence(agent_model=None, **candidates)
        agent_model = model_for_tier(tier, base_model, models)
    effective_model, reason = resolve_model_precedence(
        agent_model=agent_model or None, **candidates
    )
    policy = replace(
        policy,
        fast_model=model_for_tier("fast", effective_model, models),
        economy_model=model_for_tier("economy", effective_model, models),
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
    except AgentAccessDenied:
        raise
    except Exception as err:
        logger.warning("Failed to resolve agent persona %s: %s", agent_id, err)
        return None
