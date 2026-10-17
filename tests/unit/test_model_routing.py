"""Unit tests for model routing and the precedence chain.

These exercise the pure resolver/router in ``config_engine.cascading`` and the cost
assertion against the deterministic stub ledger, so they need no provider or database.
"""

from __future__ import annotations

import pytest
from api.services.turn_trace import TURN_STATUS_COMPLETED, TurnTraceAccumulator
from config_engine.cascading import (
    ModelRouter,
    RoutingPolicy,
    resolve_model_precedence,
)
from config_engine.exceptions import PolicyViolationError
from llm_gateway.pricing import estimate_cost_usd
from llm_gateway.stub import DeterministicStubGateway

FRONTIER = "openrouter/anthropic/claude-3.7-sonnet"
FAST = "openrouter/deepseek/deepseek-v4.1-flash"
ECONOMY = "openrouter/google/gemini-2.0-flash"
DEFAULT = "openrouter/deepseek/deepseek-v4.1-flash"


# -- precedence -----------------------------------------------------------


def test_precedence_order_is_agent_then_user_then_org_then_default() -> None:
    model, reason = resolve_model_precedence(
        agent_model="agent/model",
        request_model="turn/model",
        user_preferred_model="user/model",
        org_default_model="org/model",
        global_default_model=DEFAULT,
    )
    assert model == "agent/model"
    assert "agent" in reason


def test_this_turns_selection_beats_the_saved_preference() -> None:
    model, _ = resolve_model_precedence(
        agent_model=None,
        request_model="turn/model",
        user_preferred_model="user/model",
        org_default_model="org/model",
        global_default_model=DEFAULT,
    )
    assert model == "turn/model"


def test_saved_preference_is_used_when_no_agent_or_turn_selection() -> None:
    model, reason = resolve_model_precedence(
        agent_model=None,
        request_model=None,
        user_preferred_model="user/model",
        org_default_model="org/model",
        global_default_model=DEFAULT,
    )
    assert model == "user/model"
    assert "preference" in reason


def test_org_policy_applies_when_the_user_has_no_preference() -> None:
    model, _ = resolve_model_precedence(
        agent_model=None,
        request_model=None,
        user_preferred_model=None,
        org_default_model="org/model",
        global_default_model=DEFAULT,
    )
    assert model == "org/model"


def test_global_default_is_the_last_resort() -> None:
    model, _ = resolve_model_precedence(
        agent_model=None,
        request_model=None,
        user_preferred_model=None,
        org_default_model=None,
        global_default_model=DEFAULT,
    )
    assert model == DEFAULT


def test_disallowed_preference_is_skipped_for_the_next_candidate() -> None:
    """A user cannot escape the allowlist by saving a forbidden model."""
    model, reason = resolve_model_precedence(
        agent_model=None,
        request_model="openrouter/openai/gpt-4o",
        user_preferred_model="openrouter/anthropic/claude-3.7-sonnet",
        org_default_model="openrouter/deepseek/deepseek-v4.1-flash",
        global_default_model=DEFAULT,
        allowed_models=["openrouter/deepseek/*", "openrouter/google/*"],
    )
    assert model == "openrouter/deepseek/deepseek-v4.1-flash"
    assert "organization" in reason


def test_forbidden_pattern_beats_everything() -> None:
    model, _ = resolve_model_precedence(
        agent_model="openrouter/anthropic/claude-3.7-sonnet",
        request_model=None,
        user_preferred_model=None,
        org_default_model=None,
        global_default_model=DEFAULT,
        forbidden_models=["openrouter/anthropic/*"],
    )
    assert model == DEFAULT


def test_no_allowed_candidate_raises_a_policy_violation() -> None:
    with pytest.raises(PolicyViolationError):
        resolve_model_precedence(
            agent_model=None,
            request_model=None,
            user_preferred_model=None,
            org_default_model=None,
            global_default_model="openrouter/anthropic/claude-3.7-sonnet",
            allowed_models=["openrouter/deepseek/*"],
        )


# -- routing table --------------------------------------------------------


def _router(mode: str) -> ModelRouter:
    return ModelRouter(
        frontier_model=FRONTIER,
        policy=RoutingPolicy(mode=mode, fast_model=FAST, economy_model=ECONOMY),
    )


def test_frontier_mode_uses_one_model_for_every_role() -> None:
    router = _router("frontier")
    for role in ("react", "suggest", "summarize", "subagent:coder"):
        assert router.model_for(role) == FRONTIER


def test_balanced_mode_sends_auxiliary_roles_to_the_fast_model() -> None:
    router = _router("balanced")
    assert router.model_for("summarize") == FAST
    assert router.model_for("suggest") == FAST
    # The ReAct loop and subagents keep the frontier model.
    assert router.model_for("react") == FRONTIER
    assert router.model_for("subagent:security_auditor") == FRONTIER


def test_economy_mode_uses_the_cheap_model_for_every_role() -> None:
    router = _router("economy")
    for role in ("react", "suggest", "summarize", "subagent:tester"):
        assert router.model_for(role) == ECONOMY


def test_balanced_degrades_a_disallowed_fast_model_to_frontier() -> None:
    router = ModelRouter(
        frontier_model=FRONTIER,
        policy=RoutingPolicy(
            mode="balanced",
            fast_model="openrouter/openai/gpt-4o",
            allowed_models=("openrouter/anthropic/*",),
        ),
    )
    assert router.model_for("suggest") == FRONTIER


def test_router_mode_defaults_to_frontier_for_an_unknown_value() -> None:
    policy = RoutingPolicy.from_settings({"mode": "turbo"})
    assert policy.mode == "frontier"


def test_policy_normalizes_comma_separated_org_allowlist() -> None:
    """Comma-separated org settings must become patterns, not iterated characters."""
    policy = RoutingPolicy.from_settings(
        {"allowed_models": "openrouter/deepseek/*, openrouter/google/*"}
    )
    assert policy.allowed_models == ("openrouter/deepseek/*", "openrouter/google/*")


async def _workload_cost(router: ModelRouter, gateway: DeterministicStubGateway) -> float:
    """Run a scripted role/token workload through a router and sum the ledger cost."""
    workload = {
        "react": (1200, 400),
        "subagent:coder": (800, 200),
        "summarize": (900, 120),
        "suggest": (300, 40),
    }
    for role, (prompt_tokens, completion_tokens) in workload.items():
        await gateway.record_usage(
            "org-1", "user-1", router.model_for(role), prompt_tokens, completion_tokens
        )
    total = 0.0
    for entry in gateway.usage_ledger:
        prompt_tokens = entry["prompt_tokens"]
        completion_tokens = entry["completion_tokens"]
        assert isinstance(prompt_tokens, int)
        assert isinstance(completion_tokens, int)
        total += estimate_cost_usd(str(entry["model"]), prompt_tokens, completion_tokens)
    return total


async def test_balanced_workload_costs_less_than_frontier() -> None:
    """SG10: 'balanced' demonstrably reduces cost on a scripted workload."""
    frontier_cost = await _workload_cost(_router("frontier"), DeterministicStubGateway())
    balanced_cost = await _workload_cost(_router("balanced"), DeterministicStubGateway())

    assert balanced_cost < frontier_cost


# -- cost attribution in the trace ----------------------------------------


def test_turn_trace_persists_routing_and_usage() -> None:
    trace = TurnTraceAccumulator(session_id="s", org_id="o", turn_index=1, started_at=100)
    trace.set_routing(
        effective_model=FRONTIER,
        mode="balanced",
        role_models={"react": FRONTIER, "suggest": FAST, "summarize": FAST},
    )
    trace.record_model_usage(
        {
            "model": FAST,
            "role": "suggest",
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "cache_read_tokens": 2,
            "cache_creation_tokens": 1,
        }
    )

    finished = trace.finish(status=TURN_STATUS_COMPLETED)

    assert finished.tokens["effective_model"] == FRONTIER
    assert finished.tokens["mode"] == "balanced"
    assert finished.tokens["roles"]["suggest"] == FAST
    assert finished.tokens["usage"]["prompt_tokens"] == 10
    assert finished.tokens["usage"]["completion_tokens"] == 4
    assert finished.tokens["by_model"][FAST]["prompt_tokens"] == 10
    assert finished.tokens["by_model"][FAST]["completion_tokens"] == 4
    assert finished.tokens["by_model"][FAST]["cache_read_tokens"] == 2
    assert finished.tokens["by_model"][FAST]["cache_creation_tokens"] == 1
