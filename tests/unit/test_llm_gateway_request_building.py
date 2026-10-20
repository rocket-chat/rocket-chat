"""How a ModelRequest becomes LiteLLM arguments, and how provider chunks become stream chunks."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest
from api.services.settings import save_org_config
from llm_gateway.gateway import LiteLLMGateway
from llm_gateway.reasoning import ReasoningStreamFilter

from specifications.interfaces.llm import ChatMessage, ModelRequest, current_model_scope
from tests.turn_harness import run_turn


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> LiteLLMGateway:
    monkeypatch.setenv("ROCKET_MODEL_MAX_RETRIES", "2")
    return LiteLLMGateway()


def _request(**overrides: object) -> ModelRequest:
    return ModelRequest(
        model="openrouter/acme/model",
        messages=[ChatMessage(role="user", content="hi")],
        **overrides,
    )  # type: ignore[arg-type]


def test_a_plain_request_streams_with_the_gateway_default_retries(gateway: LiteLLMGateway) -> None:
    kwargs = gateway._completion_kwargs("openrouter/acme/model", _request())

    assert kwargs["stream"] is True and kwargs["num_retries"] == 2
    assert "max_tokens" not in kwargs and "tools" not in kwargs and "response_format" not in kwargs


def test_request_options_are_forwarded_and_override_the_default_retries(
    gateway: LiteLLMGateway,
) -> None:
    options = gateway._generation_options(
        _request(
            max_tokens=500,
            thinking_budget_tokens=2048,
            reasoning_effort="high",
            max_retries=0,
            fallback_models=["openrouter/other/model"],
            response_schema={"type": "object"},
            response_schema_name="answer",
        )
    )

    assert options["max_tokens"] == 500
    assert options["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert options["reasoning_effort"] == "high" and options["num_retries"] == 0
    assert options["fallbacks"] == ["openrouter/other/model"]
    assert options["response_format"]["json_schema"]["name"] == "answer"
    assert options["response_format"]["json_schema"]["strict"] is True


def test_usage_includes_prompt_cache_counts_from_either_provider_shape() -> None:
    anthropic_style = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cache_read_input_tokens=7,
            cache_creation_input_tokens=3,
        )
    )
    openai_style = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            prompt_tokens_details=SimpleNamespace(cached_tokens=4),
        )
    )

    first = LiteLLMGateway._usage_from_chunk(anthropic_style)
    second = LiteLLMGateway._usage_from_chunk(openai_style)

    assert first is not None and first["cache_read_tokens"] == 7
    assert first["cache_creation_tokens"] == 3
    assert second is not None and second["cache_read_tokens"] == 4
    assert "cache_creation_tokens" not in second
    assert LiteLLMGateway._usage_from_chunk(SimpleNamespace(usage=None)) is None


def test_a_delta_yields_reasoning_then_text_then_tool_calls() -> None:
    delta = SimpleNamespace(
        reasoning_content="considering",
        content="Answer <think>hidden</think>done",
        tool_calls=[
            SimpleNamespace(
                index=1,
                id="call_1",
                type="function",
                function=SimpleNamespace(name="file_read", arguments='{"path":"a"}'),
            )
        ],
    )
    flt = ReasoningStreamFilter()

    chunks = list(LiteLLMGateway._delta_chunks(delta, flt))
    chunks += list(LiteLLMGateway._filtered_chunks(flt.flush()))

    assert chunks[0].reasoning_delta == "considering"
    text = "".join(c.text_delta or "" for c in chunks)
    reasoning = "".join(c.reasoning_delta or "" for c in chunks)
    assert text == "Answer done" and "hidden" in reasoning
    tool_call = next(c.tool_call_delta for c in chunks if c.tool_call_delta)
    assert tool_call["index"] == 1 and tool_call["function"]["name"] == "file_read"


def test_a_provider_authentication_error_is_an_actionable_error_not_text() -> None:
    import litellm

    refused = litellm.exceptions.AuthenticationError("bad key", "openrouter", "acme/model")
    missing = litellm.exceptions.NotFoundError("no such model", "openrouter", "acme/model")

    auth = LiteLLMGateway._provider_error_chunks("openrouter/acme/model", refused)
    absent = LiteLLMGateway._provider_error_chunks("openrouter/acme/model", missing)

    for chunks in (auth, absent):
        assert len(chunks) == 1 and chunks[0].finish_reason == "error"
        assert chunks[0].text_delta is None, "never shown or stored as the model's answer"
    assert "Settings > Models" in (auth[0].error or "") and "bad key" in (auth[0].error or "")
    assert "was not found" in (absent[0].error or "")


@pytest.mark.setting("models.provider_preferences")
async def test_provider_preferences_pin_an_openrouter_model_and_keep_data_rules() -> None:
    org = "wire_provider_preferences"
    pinned = {"order": ["deepseek"], "allow_fallbacks": False}
    await save_org_config(None, org, "models", {"provider_preferences": {"deepseek/*": pinned}}, [])
    outcome = await run_turn(org, model_choice="openrouter/deepseek/deepseek-v4-pro")
    scope = outcome.model.scopes[0]
    assert scope.provider_preferences == (("deepseek/*", pinned),)

    gateway = LiteLLMGateway()
    token = current_model_scope.set(dataclasses.replace(scope, zero_retention=True))
    try:
        kwargs = gateway._completion_kwargs(
            "openrouter/deepseek/deepseek-v4-pro",
            dataclasses.replace(
                _request(zero_retention=True), model="openrouter/deepseek/deepseek-v4-pro"
            ),
        )
        other = gateway._completion_kwargs("openrouter/acme/model", _request())
    finally:
        current_model_scope.reset(token)

    provider = kwargs["extra_body"]["provider"]
    assert provider == {"data_collection": "deny", **pinned}
    assert kwargs["extra_body"]["session_id"] == scope.session_id
    assert "provider" not in other.get("extra_body", {}) or other["extra_body"]["provider"] == {
        "data_collection": "deny"
    }
