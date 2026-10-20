"""Model data-governance translation.

An org can require the provider not to retain the request and can pin the region it is
served from. Those are org policy, so they are translated to provider-native LiteLLM
kwargs in one place; this asserts the translation, including the always-safe metadata and
the provider-specific knobs.
"""

from __future__ import annotations

import pytest
from llm_gateway.errors import DataGovernanceError
from llm_gateway.gateway import LiteLLMGateway

from specifications.interfaces.llm import ChatMessage, ModelRequest


def _request(model: str, **overrides: object) -> ModelRequest:
    return ModelRequest(
        model=model,
        messages=[ChatMessage(role="user", content="hello")],
        **overrides,  # type: ignore[arg-type]
    )


def test_no_policy_emits_no_governance_kwargs() -> None:
    kwargs = LiteLLMGateway._data_governance_kwargs(
        "openrouter/anthropic/claude-3.7-sonnet", _request("openrouter/anthropic/x")
    )
    assert kwargs == {}


def test_zero_retention_openrouter_denies_data_collection() -> None:
    kwargs = LiteLLMGateway._data_governance_kwargs(
        "openrouter/anthropic/claude-3.7-sonnet",
        _request("openrouter/anthropic/x", zero_retention=True),
    )
    assert kwargs["metadata"] == {"zero_retention": True}
    assert kwargs["extra_body"] == {"provider": {"data_collection": "deny"}}


def test_region_pins_vertex_location() -> None:
    kwargs = LiteLLMGateway._data_governance_kwargs(
        "vertex_ai/gemini-2.0-flash",
        _request("vertex_ai/gemini-2.0-flash", region="europe-west1"),
    )
    assert kwargs["metadata"] == {"region": "europe-west1"}
    assert kwargs["vertex_location"] == "europe-west1"
    assert "extra_body" not in kwargs


def test_region_pins_aws_region_for_bedrock() -> None:
    kwargs = LiteLLMGateway._data_governance_kwargs(
        "bedrock/anthropic.claude-3",
        _request("bedrock/anthropic.claude-3", region="eu-central-1"),
    )
    assert kwargs["aws_region_name"] == "eu-central-1"


def test_a_provider_that_cannot_honour_zero_retention_is_refused() -> None:
    with pytest.raises(DataGovernanceError, match="Zero retention is required"):
        LiteLLMGateway._data_governance_kwargs(
            "openai/gpt-4o", _request("openai/gpt-4o", zero_retention=True)
        )


def test_a_provider_that_cannot_honour_a_region_is_refused() -> None:
    with pytest.raises(DataGovernanceError, match="region"):
        LiteLLMGateway._data_governance_kwargs(
            "openrouter/x/y", _request("openrouter/x/y", region="eu")
        )


def test_self_hosted_models_satisfy_both_rules() -> None:
    kwargs = LiteLLMGateway._data_governance_kwargs(
        "ollama/llama3.1", _request("ollama/llama3.1", zero_retention=True, region="eu")
    )
    assert kwargs["metadata"] == {"zero_retention": True, "region": "eu"}
    assert "extra_body" not in kwargs


async def test_a_refused_call_is_an_error_chunk_and_never_reaches_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from llm_gateway import gateway as gateway_module

    called: list[object] = []

    async def _never(**kwargs: object) -> object:
        called.append(kwargs)
        raise AssertionError("the provider must not be called")

    monkeypatch.setattr(gateway_module.litellm, "acompletion", _never)

    chunks = [
        chunk
        async for chunk in LiteLLMGateway().chat_stream(
            _request("openai/gpt-4o", zero_retention=True)
        )
    ]

    assert called == []
    assert chunks[-1].finish_reason == "error" and "Zero retention is required" in (
        chunks[-1].error or ""
    )


async def test_the_turn_scope_applies_the_orgs_data_rules_to_requests_that_do_not_set_them() -> (
    None
):
    """Titles, summaries and follow-ups build their own requests; the scope still protects them."""
    from typing import Any

    from llm_gateway import gateway as gateway_module

    from specifications.interfaces.llm import ModelScope, current_model_scope

    seen: list[dict[str, Any]] = []

    async def _fake_completion(**kwargs: Any) -> Any:
        seen.append(kwargs)

        async def _empty() -> Any:
            return
            yield  # pragma: no cover - makes this an async generator

        return _empty()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(gateway_module.litellm, "acompletion", _fake_completion)
    try:
        for model, rules in (
            ("vertex_ai/gemini-2.0-flash", {"region": "europe-west1"}),
            ("openrouter/deepseek/deepseek-v4-pro", {"zero_retention": True}),
        ):
            token = current_model_scope.set(ModelScope(org_id="o", user_id="u", **rules))  # type: ignore[arg-type]
            try:
                async for _ in LiteLLMGateway().chat_stream(_request(model)):
                    pass
            finally:
                current_model_scope.reset(token)
    finally:
        monkeypatch.undo()

    assert seen[0]["metadata"] == {"region": "europe-west1"}
    assert seen[0]["vertex_location"] == "europe-west1"
    assert seen[1]["metadata"] == {"zero_retention": True}
    assert seen[1]["extra_body"]["provider"]["data_collection"] == "deny"
