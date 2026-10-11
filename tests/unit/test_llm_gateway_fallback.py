"""Unit tests for provider resilience (LiteLLM delegation) and prompt caching."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import litellm
import pytest
import pytest_asyncio
from llm_gateway.gateway import LiteLLMGateway

from specifications.interfaces.llm import ChatMessage, ModelRequest, ToolDefinition


def _chunk(text: str, usage: Any = None) -> Any:
    chunk = MagicMock()
    choice = MagicMock()
    choice.delta.content = text
    choice.delta.reasoning_content = None
    choice.delta.thinking = None
    choice.delta.tool_calls = None
    choice.finish_reason = "stop"
    chunk.choices = [choice]
    chunk.usage = usage
    return chunk


@pytest_asyncio.fixture
async def capturing(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch litellm.acompletion, capturing kwargs and streaming a simple reply."""
    captured: dict[str, Any] = {}

    async def mock_acompletion(**kwargs: Any) -> AsyncIterator[Any]:
        captured.clear()
        captured.update(kwargs)

        async def _gen() -> AsyncIterator[Any]:
            yield _chunk("ok")

        return _gen()

    monkeypatch.setattr(litellm, "acompletion", mock_acompletion)
    return captured


async def _drain(request: ModelRequest, gateway: LiteLLMGateway | None = None) -> str:
    gw = gateway or LiteLLMGateway()
    return "".join(
        [chunk.text_delta async for chunk in gw.chat_stream(request) if chunk.text_delta]
    )


@pytest.mark.asyncio
async def test_retries_and_fallbacks_delegated_to_litellm(
    capturing: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROCKET_MODEL_MAX_RETRIES", "3")
    request = ModelRequest(
        model="openrouter/primary",
        fallback_models=["openrouter/secondary"],
        messages=[ChatMessage(role="user", content="hi")],
    )
    await _drain(request)

    assert capturing["num_retries"] == 3
    assert capturing["fallbacks"] == ["openrouter/secondary"]


@pytest.mark.asyncio
async def test_per_request_retries_override(capturing: dict[str, Any]) -> None:
    request = ModelRequest(
        model="openrouter/primary",
        max_retries=0,
        messages=[ChatMessage(role="user", content="hi")],
    )
    await _drain(request)
    assert capturing["num_retries"] == 0
    assert "fallbacks" not in capturing


@pytest.mark.asyncio
async def test_fatal_error_surfaces_error_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    async def mock_acompletion(**kwargs: Any) -> AsyncIterator[Any]:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(litellm, "acompletion", mock_acompletion)

    gateway = LiteLLMGateway()
    request = ModelRequest(model="openrouter/x", messages=[ChatMessage(role="user", content="hi")])
    chunks = [chunk async for chunk in gateway.chat_stream(request)]

    assert chunks[-1].is_finished is True
    assert chunks[-1].finish_reason == "error"
    assert chunks[-1].error is not None and "provider exploded" in chunks[-1].error
    assert "".join(c.text_delta for c in chunks if c.text_delta) == ""


@pytest.mark.asyncio
async def test_anthropic_messages_get_cache_breakpoint(capturing: dict[str, Any]) -> None:
    request = ModelRequest(
        model="anthropic/claude-3-7-sonnet",
        messages=[
            ChatMessage(role="system", content="STABLE PREFIX", cacheable=True),
            ChatMessage(role="system", content="volatile context"),
            ChatMessage(role="user", content="hi"),
        ],
        tools=[
            ToolDefinition(name="t", description="d", parameters_json_schema={"type": "object"})
        ],
    )
    await _drain(request)

    messages = capturing["messages"]
    assert messages[0]["content"] == [
        {"type": "text", "text": "STABLE PREFIX", "cache_control": {"type": "ephemeral"}}
    ]
    # Volatile system content must NOT carry a cache breakpoint.
    assert messages[1]["content"] == "volatile context"
    assert capturing["tools"][-1]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_non_anthropic_provider_omits_explicit_markers(capturing: dict[str, Any]) -> None:
    request = ModelRequest(
        model="openrouter/deepseek/deepseek-v4.1-flash",
        messages=[
            ChatMessage(role="system", content="STABLE PREFIX", cacheable=True),
            ChatMessage(role="user", content="hi"),
        ],
    )
    await _drain(request)

    # Automatic prefix caching still benefits from stable ordering, but no
    # block-level markers are emitted for providers that do not support them.
    assert capturing["messages"][0]["content"] == "STABLE PREFIX"


@pytest.mark.asyncio
async def test_cache_can_be_disabled(capturing: dict[str, Any]) -> None:
    request = ModelRequest(
        model="anthropic/claude-3-7-sonnet",
        enable_prompt_cache=False,
        messages=[ChatMessage(role="system", content="S", cacheable=True)],
    )
    await _drain(request)
    assert capturing["messages"][0]["content"] == "S"


@pytest.mark.asyncio
async def test_cache_tokens_are_aggregated_in_ledger() -> None:
    gateway = LiteLLMGateway()
    await gateway.record_usage(
        tenant_org_id="org",
        tenant_user_id="usr",
        model="anthropic/claude-3-7-sonnet",
        prompt_tokens=100,
        completion_tokens=10,
        cache_read_tokens=80,
        cache_creation_tokens=20,
    )
    org_summary = gateway.get_org_usage_summary("org")
    user_summary = gateway.get_user_usage_summary("usr")
    assert org_summary["cache_read_tokens"] == 80
    assert org_summary["cache_creation_tokens"] == 20
    assert user_summary["cache_read_tokens"] == 80
