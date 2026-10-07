from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from llm_gateway.gateway import LiteLLMGateway

from specifications.interfaces.llm import (
    BYOKCredentials,
    ChatMessage,
    ModelRequest,
    ToolDefinition,
)


@pytest.mark.asyncio
async def test_gateway_credential_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, Any] = {}

    async def mock_acompletion(**kwargs: Any) -> AsyncIterator[Any]:
        nonlocal captured_kwargs
        captured_kwargs = kwargs

        async def _gen() -> AsyncIterator[Any]:
            mock_chunk = MagicMock()
            mock_choice = MagicMock()
            mock_choice.delta.content = "test response"
            mock_choice.delta.reasoning_content = None
            mock_choice.delta.thinking = None
            mock_choice.delta.tool_calls = None
            mock_choice.finish_reason = "stop"
            mock_chunk.choices = [mock_choice]
            mock_chunk.usage = None
            yield mock_chunk

        return _gen()

    import litellm

    monkeypatch.setattr(litellm, "acompletion", mock_acompletion)

    gateway = LiteLLMGateway()
    creds = BYOKCredentials(
        provider="openrouter",
        api_key="sk-or-test-key",
        api_base="https://openrouter.ai/api/v1",
        custom_headers={"HTTP-Referer": "https://rocket.chat"},
    )
    request = ModelRequest(
        model="openrouter/anthropic/claude-3.7-sonnet",
        messages=[ChatMessage(role="user", content="hello")],
        credentials=creds,
        thinking_budget_tokens=4096,
        reasoning_effort="high",
        tools=[
            ToolDefinition(
                name="test_tool",
                description="test",
                parameters_json_schema={"type": "object"},
            )
        ],
    )

    chunks = []
    async for chunk in gateway.chat_stream(request):
        chunks.append(chunk)

    assert captured_kwargs["api_key"] == "sk-or-test-key"
    assert captured_kwargs["api_base"] == "https://openrouter.ai/api/v1"
    assert captured_kwargs["extra_headers"] == {"HTTP-Referer": "https://rocket.chat"}
    assert captured_kwargs["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert captured_kwargs["reasoning_effort"] == "high"
    assert len(captured_kwargs["tools"]) == 1
    assert captured_kwargs["tools"][0]["function"]["name"] == "test_tool"

    text_received = "".join(c.text_delta for c in chunks if c.text_delta)
    assert text_received == "test response"


@pytest.mark.asyncio
async def test_gateway_reasoning_and_tool_streaming(monkeypatch: pytest.MonkeyPatch) -> None:
    async def mock_acompletion(**kwargs: Any) -> AsyncIterator[Any]:
        async def _gen() -> AsyncIterator[Any]:
            # Chunk 1: Provider-native reasoning
            c1 = MagicMock()
            ch1 = MagicMock()
            ch1.delta.content = None
            ch1.delta.reasoning_content = "Planning the approach..."
            ch1.delta.thinking = None
            ch1.delta.tool_calls = None
            ch1.finish_reason = None
            c1.choices = [ch1]
            c1.usage = None
            yield c1

            # Chunk 2: In-band <think> block inside text
            c2 = MagicMock()
            ch2 = MagicMock()
            ch2.delta.content = "<think>Validating schema.</think>Calling tool now."
            ch2.delta.reasoning_content = None
            ch2.delta.thinking = None
            ch2.delta.tool_calls = None
            ch2.finish_reason = None
            c2.choices = [ch2]
            c2.usage = None
            yield c2

            # Chunk 3: Tool call delta
            c3 = MagicMock()
            ch3 = MagicMock()
            ch3.delta.content = None
            ch3.delta.reasoning_content = None
            ch3.delta.thinking = None
            mock_tc = MagicMock()
            mock_tc.index = 0
            mock_tc.id = "call_abc"
            mock_tc.type = "function"
            mock_tc.function.name = "web_fetch"
            mock_tc.function.arguments = '{"url": "https://httpbin.org/json"}'
            ch3.delta.tool_calls = [mock_tc]
            ch3.finish_reason = "tool_calls"
            c3.choices = [ch3]
            c3.usage = MagicMock(prompt_tokens=10, completion_tokens=25, total_tokens=35)
            yield c3

        return _gen()

    import litellm

    monkeypatch.setattr(litellm, "acompletion", mock_acompletion)

    gateway = LiteLLMGateway()
    request = ModelRequest(
        model="openrouter/deepseek/deepseek-r1",
        messages=[ChatMessage(role="user", content="Fetch data")],
    )

    reasoning_parts = []
    text_parts = []
    tool_calls = []
    finished = False

    async for chunk in gateway.chat_stream(request):
        if chunk.reasoning_delta:
            reasoning_parts.append(chunk.reasoning_delta)
        if chunk.text_delta:
            text_parts.append(chunk.text_delta)
        if chunk.tool_call_delta:
            tool_calls.append(chunk.tool_call_delta)
        if chunk.is_finished:
            finished = True

    full_reasoning = "".join(reasoning_parts)
    full_text = "".join(text_parts)

    assert "Planning the approach..." in full_reasoning
    assert "Validating schema." in full_reasoning
    assert "<think>" not in full_reasoning
    assert "<think>" not in full_text
    assert full_text == "Calling tool now."

    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "web_fetch"
    assert "https://httpbin.org/json" in tool_calls[0]["function"]["arguments"]
    assert finished is True


@pytest.mark.asyncio
async def test_gateway_credential_and_header_sanitization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, Any] = {}

    async def mock_acompletion(**kwargs: Any) -> AsyncIterator[Any]:
        nonlocal captured_kwargs
        captured_kwargs = kwargs

        async def _gen() -> AsyncIterator[Any]:
            mock_chunk = MagicMock()
            mock_choice = MagicMock()
            mock_choice.delta.content = "sanitized response"
            mock_choice.delta.reasoning_content = None
            mock_choice.delta.thinking = None
            mock_choice.delta.tool_calls = None
            mock_choice.finish_reason = "stop"
            mock_chunk.choices = [mock_choice]
            mock_chunk.usage = None
            yield mock_chunk

        return _gen()

    import litellm

    monkeypatch.setattr(litellm, "acompletion", mock_acompletion)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dirty-key\r\n")

    gateway = LiteLLMGateway()

    creds = BYOKCredentials(
        provider="openrouter",
        api_key="sk-or-byok-key\n",
        api_base="https://openrouter.ai/api/v1\r",
        custom_headers={"X-Custom-Header\r": "value-with-newline\n"},
    )
    request = ModelRequest(
        model="  openrouter/deepseek/deepseek-chat  \n",
        messages=[ChatMessage(role="user", content="hello")],
        credentials=creds,
    )

    async for _ in gateway.chat_stream(request):
        pass

    assert captured_kwargs["model"] == "openrouter/deepseek/deepseek-chat"
    assert captured_kwargs["api_key"] == "sk-or-byok-key"
    assert captured_kwargs["api_base"] == "https://openrouter.ai/api/v1"
    assert captured_kwargs["extra_headers"] == {"X-Custom-Header": "value-with-newline"}
