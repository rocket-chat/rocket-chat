"""Premium defaults: model facts and prices, reasoning detection, tuned generation values, and
the settings P6 added, each shown to change what the platform does."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from agent_core.context import ContextManager, ExecutionContext, current_execution_ctx
from api.services.memory import MemoryPolicy, configured_embedding_model
from api.services.model_defaults import model_for_tier, organization_default_model
from api.services.sandbox_catalogue import SandboxEntry
from api.services.settings import effective_settings, save_org_config
from api.services.skills_lookup import _find_visible_skill, list_visible_skills
from api.services.starter_content import STARTER_SKILL_NAMES
from api.services.turn_service import _output_budget, _temperature
from api.turns.provisioning import _resources
from api.turns.shared import _turn_service
from config_engine.model_defaults import PROVIDER_TIERS
from config_engine.registry import STARTER_SKILLS
from llm_gateway.model_facts import facts_for, prices_for
from llm_gateway.pricing import estimate_cost_usd

from specifications.interfaces.agent import GenerationParams, TurnPolicy
from specifications.interfaces.llm import is_reasoning_model


async def _plan(org: str, **kwargs: Any) -> Any:
    return await _turn_service.plan_turn(
        db_mgr=None,
        org_id=org,
        user_id="u1",
        agent_id=None,
        global_default_model=kwargs.pop("global_default_model", "anthropic/claude-sonnet-5-5"),
        **kwargs,
    )


# --- model facts -------------------------------------------------------------------------


def test_every_default_model_has_a_price_and_a_window() -> None:
    for tiers in PROVIDER_TIERS.values():
        for model in tiers.models():
            facts = facts_for(model)
            assert facts is not None, f"{model} is missing from llm_gateway.model_facts"
            assert facts.context_window > 0 and facts.max_output_tokens > 0
            assert facts.input_usd > 0 and facts.output_usd > 0


def test_claude_tiers_are_priced_apart() -> None:
    opus = estimate_cost_usd("anthropic/claude-opus-5-5", 1_000_000, 0)
    haiku = estimate_cost_usd("openrouter/anthropic/claude-haiku-5.5", 1_000_000, 0)
    assert opus == 5.0 and haiku == 1.0
    assert prices_for("some/unknown-model") == (1.0, 3.0, 0.5, 1.0), "never accounted as free"


@pytest.mark.parametrize(
    ("model", "reasons"),
    [
        ("anthropic/claude-sonnet-5-5", True),
        ("openrouter/anthropic/claude-haiku-5.5", True),
        ("claude-3-7-sonnet", True),
        ("claude-3-5-sonnet", False),
        ("openai/gpt-6.1-sol", True),
        ("gpt-4o", False),
        ("o4-mini", True),
        ("gemini/gemini-3.8-flash", True),
        ("gemini-2.0-flash", False),
        ("deepseek/deepseek-r1", True),
    ],
)
def test_reasoning_models_are_recognised(model: str, reasons: bool) -> None:
    assert is_reasoning_model(model) is reasons


# --- tuned generation values -------------------------------------------------------------


def test_a_reasoning_model_gets_the_providers_temperature_unless_one_was_chosen() -> None:
    platform = {"temperature": 0.2}
    assert _temperature(platform, None, reasoning=True) is None
    assert _temperature(platform, None, reasoning=False) == 0.2
    assert _temperature({"temperature": 0.7}, None, reasoning=True) == 0.7
    assert _temperature(platform, SimpleNamespace(temperature=0.2), reasoning=True) == 0.2


def test_the_output_budget_is_the_smaller_of_the_setting_and_the_models_own() -> None:
    spec = SimpleNamespace(max_output_tokens=64_000)
    models = {"max_tokens": 32_000, "output_tokens": {}}
    assert _output_budget("anthropic/claude-opus-5-5", None, models, spec) == 32_000
    assert _output_budget("x", None, models, SimpleNamespace(max_output_tokens=16_384)) == 16_384
    assert _output_budget("x", SimpleNamespace(max_tokens=4_000), models, spec) == 4_000


# --- settings ----------------------------------------------------------------------------


@pytest.mark.setting("models.default_provider")
async def test_the_default_provider_picks_whose_models_are_the_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    org = "p6_default_provider"
    models = dict(await effective_settings(None, org, "models"))
    assert await organization_default_model(None, org, models, "env/default") == "env/default"

    models["default_provider"] = "openai"
    assert (
        await organization_default_model(None, org, models, "env/default") == "openai/gpt-6.1-sol"
    )


@pytest.mark.setting("models.frontier_model")
def test_the_frontier_model_setting_replaces_the_providers_frontier() -> None:
    base = "anthropic/claude-sonnet-5-5"
    assert model_for_tier("frontier", base, {}) == "anthropic/claude-opus-5-5"
    assert model_for_tier("frontier", base, {"frontier_model": "my/frontier"}) == "my/frontier"


@pytest.mark.setting("models.automatic_fallbacks")
async def test_automatic_fallbacks_fail_over_to_the_same_tier_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENROUTER_API_KEY", "r")
    org = "p6_fallbacks"
    on = await _plan(org, request_model="anthropic/claude-sonnet-5-5")
    assert on.policy.generation.fallback_models == ("openrouter/anthropic/claude-sonnet-5.5",)

    await save_org_config(None, org, "models", {"automatic_fallbacks": False}, [])
    off = await _plan(org, request_model="anthropic/claude-sonnet-5-5")
    assert off.policy.generation.fallback_models == ()


@pytest.mark.setting("models.context_prune_percent")
@pytest.mark.setting("models.context_compact_percent")
async def test_the_context_thresholds_follow_the_organization() -> None:
    org = "p6_context"
    await save_org_config(
        None, org, "models", {"context_prune_percent": 50, "context_compact_percent": 60}, []
    )
    generation = (await _plan(org)).policy.generation
    assert (generation.context_prune_ratio, generation.context_compact_ratio) == (0.5, 0.6)

    manager = ContextManager()
    assert manager.ratios() == (0.70, 0.85), "outside a turn: the platform's own"
    ctx = ExecutionContext(
        session_id="s",
        event_queue=asyncio.Queue(),
        parent_model="m",
        policy=TurnPolicy(generation=generation),
    )
    token = current_execution_ctx.set(ctx)
    try:
        assert manager.ratios() == (0.5, 0.6)
    finally:
        current_execution_ctx.reset(token)


def test_compaction_is_kept_above_pruning() -> None:
    from api.services.turn_service import _context_ratios

    ratios = _context_ratios({"context_prune_percent": 80, "context_compact_percent": 60})
    assert ratios["context_compact_ratio"] > ratios["context_prune_ratio"]


@pytest.mark.setting("memory.embedding_model")
def test_the_embedding_model_is_the_setting_else_a_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert configured_embedding_model({}) == "", "no provider offers one: keyword recall"
    assert MemoryPolicy.from_settings({"embedding_model": "my/embed"}).embedding_model == "my/embed"
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    assert configured_embedding_model({}) == "text-embedding-3-small"


@pytest.mark.setting("sandboxes.disk_size")
def test_the_workspace_disk_comes_from_the_setting_unless_the_type_sets_one() -> None:
    config = {"cpu_limit": 2, "memory_limit": "4Gi", "disk_size": "50Gi"}
    assert _resources(None, config).disk_size == "50Gi"
    sized = SandboxEntry(id="t", org_id="o", name="Big", image="i:1", disk_size="100Gi")
    assert _resources(sized, config).disk_size == "100Gi"


@pytest.mark.setting("general.starter_skills")
async def test_starter_skills_are_offered_until_the_organization_turns_them_off() -> None:
    assert STARTER_SKILLS == STARTER_SKILL_NAMES, "the registry default lists every shipped skill"
    org = "p6_starters"
    names = [name for name, _ in await list_visible_skills(None, org, "u1")]
    assert "review-diff" in names
    assert (await _find_visible_skill(None, org, "u1", "review-diff")) is not None

    await save_org_config(None, org, "general", {"starter_skills": ["write-tests"]}, [])
    names = [name for name, _ in await list_visible_skills(None, org, "u1")]
    assert "review-diff" not in names and "write-tests" in names
    assert (await _find_visible_skill(None, org, "u1", "review-diff")) is None


@pytest.mark.setting("general.plan_critic")
async def test_the_plan_critic_is_carried_on_the_turn_policy() -> None:
    org = "p6_plan_critic"
    assert (await _plan(org)).policy.plan_critic is False
    await save_org_config(None, org, "general", {"plan_critic": True}, [])
    assert (await _plan(org)).policy.plan_critic is True


def test_generation_params_default_to_the_managers_thresholds() -> None:
    params = GenerationParams()
    assert params.context_prune_ratio == 0.0 and params.context_compact_ratio == 0.0
