"""Unit tests for model catalogue, reasoning policies, and output budgets."""

from __future__ import annotations

from agent_core.context import ContextManager
from api.services.model_catalogue import ModelCatalogue
from api.services.settings import save_org_config
from llm_gateway.gateway import LiteLLMGateway

from specifications.interfaces.llm import ModelRequest, ModelSpec
from tests.turn_harness import run_turn


def test_model_catalogue_resolves_specs_for_families() -> None:
    catalogue = ModelCatalogue()

    o3 = catalogue.get_model_spec("openai/o3-mini")
    assert o3.supports_reasoning is True
    assert o3.max_output_tokens == 64_000
    assert o3.context_window == 200_000

    c37 = catalogue.get_model_spec("anthropic/claude-3-7-sonnet")
    assert c37.supports_reasoning is True
    assert c37.max_output_tokens == 64_000

    r1 = catalogue.get_model_spec("deepseek/deepseek-r1")
    assert r1.supports_reasoning is True
    assert r1.max_output_tokens == 64_000

    gpt4o = catalogue.get_model_spec("openai/gpt-4o")
    assert gpt4o.supports_reasoning is False
    assert gpt4o.max_output_tokens == 16_384
    assert gpt4o.context_window == 128_000


def test_context_manager_consults_catalogue_before_defaults() -> None:
    catalogue = ModelCatalogue()
    catalogue.register(
        ModelSpec(
            id="custom/super-model",
            context_window=500_000,
            max_output_tokens=32_000,
        )
    )

    ctx_mgr = ContextManager(catalogue=catalogue)
    limit = ctx_mgr.get_model_context_limit("custom/super-model")
    assert limit == 500_000


def test_gateway_maps_reasoning_effort_and_thinking_budget() -> None:
    gateway = LiteLLMGateway()

    # OpenAI o3 with reasoning_effort
    req_openai = ModelRequest(
        model="openai/o3-mini",
        messages=[],
        reasoning_effort="medium",
        max_tokens=64_000,
    )
    opts_openai = gateway._generation_options(req_openai)
    assert opts_openai.get("reasoning_effort") == "medium"
    assert opts_openai.get("max_tokens") == 64_000

    # Anthropic with thinking_budget_tokens
    req_anthropic = ModelRequest(
        model="anthropic/claude-3-7-sonnet",
        messages=[],
        thinking_budget_tokens=16_000,
        max_tokens=64_000,
    )
    opts_anthropic = gateway._generation_options(req_anthropic)
    assert opts_anthropic.get("thinking") == {
        "type": "enabled",
        "budget_tokens": 16_000,
    }
    assert opts_anthropic.get("max_tokens") == 64_000


async def test_turn_harness_asserts_max_tokens_and_reasoning() -> None:
    org = "harness_output_budget"
    await save_org_config(
        None,
        org,
        "models",
        {
            "output_tokens": {"openai/o3-mini": 50000},
        },
        [],
    )
    outcome = await run_turn(
        org=org,
        model_choice="openai/o3-mini",
        steps=[{"kind": "say", "text": "Hello! Finished."}],
        prompt="Explain architecture",
    )
    assert outcome.session.session_id == f"session_{org}"
    assert outcome.model.requests
    agent_request = outcome.model.requests[0]
    assert agent_request.max_tokens == 50000
    assert agent_request.reasoning_effort == "medium"
