"""Model data-governance translation.

An org can require the provider not to retain the request and can pin the region it is
served from. Those are org policy, so they are translated to provider-native LiteLLM
kwargs in one place; this asserts the translation, including the always-safe metadata and
the provider-specific knobs.
"""

from __future__ import annotations

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


def test_unknown_provider_still_receives_metadata() -> None:
    kwargs = LiteLLMGateway._data_governance_kwargs(
        "some-provider/foo",
        _request("some-provider/foo", zero_retention=True, region="eu"),
    )
    assert kwargs["metadata"] == {"zero_retention": True, "region": "eu"}
    # No provider-native knob is invented for an unknown provider; metadata carries intent.
    assert "extra_body" not in kwargs
    assert "vertex_location" not in kwargs
    assert "aws_region_name" not in kwargs


async def test_the_turn_scope_applies_the_orgs_data_rules_to_requests_that_do_not_set_them() -> (
    None
):
    """Titles, summaries and follow-ups build their own requests; the scope still protects them."""
    from typing import Any

    import pytest
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
    token = current_model_scope.set(
        ModelScope(org_id="o", user_id="u", zero_retention=True, region="europe-west1")
    )
    try:
        async for _ in LiteLLMGateway().chat_stream(_request("vertex_ai/gemini-2.0-flash")):
            pass
    finally:
        current_model_scope.reset(token)
        monkeypatch.undo()

    assert seen[0]["metadata"] == {"zero_retention": True, "region": "europe-west1"}
    assert seen[0]["vertex_location"] == "europe-west1"
