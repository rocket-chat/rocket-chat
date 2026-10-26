"""Reasoning goes back to the providers that require it, in the form each one requires.

Anthropic-style extended thinking needs its signed thinking blocks with the tool calls they led
to (without them LiteLLM either drops thinking or the provider rejects the request); DeepSeek's
reasoning models need ``reasoning_content`` during a tool loop; other families take none.
Checked against LiteLLM's own request translation, offline.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from litellm.llms.anthropic.chat.transformation import AnthropicConfig
from llm_gateway.gateway import LiteLLMGateway, reasoning_replay
from llm_gateway.reasoning import merge_reasoning_blocks
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import ChatMessage, LLMGatewayProtocol, ModelRequest, StreamChunk

SIGNED = {"type": "thinking", "thinking": "I should list it.", "signature": "sig-abc"}


def _conversation() -> list[ChatMessage]:
    call = [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}]
    return [
        ChatMessage(role="system", content="S"),
        ChatMessage(role="user", content="old question"),
        ChatMessage(
            role="assistant",
            content=None,
            reasoning="old thought",
            tool_calls=[
                {"id": "c0", "type": "function", "function": {"name": "t", "arguments": "{}"}}
            ],
        ),
        ChatMessage(role="tool", content="r0", tool_call_id="c0"),
        ChatMessage(role="assistant", content="old answer"),
        ChatMessage(role="user", content="new question"),
        ChatMessage(
            role="assistant",
            content="Let me look.",
            reasoning="I should list it.",
            reasoning_blocks=[SIGNED],
            tool_calls=call,
        ),
        ChatMessage(role="tool", content="r1", tool_call_id="c1"),
    ]


def test_streamed_pieces_become_whole_signed_blocks() -> None:
    blocks: list[dict[str, Any]] = []
    merge_reasoning_blocks(blocks, [{"type": "thinking", "thinking": "I should "}])
    merge_reasoning_blocks(blocks, [{"type": "thinking", "thinking": "list it."}])
    merge_reasoning_blocks(blocks, [{"type": "thinking", "thinking": "", "signature": "sig-abc"}])
    merge_reasoning_blocks(blocks, [{"type": "redacted_thinking", "data": "xyz"}])

    assert blocks == [SIGNED, {"type": "redacted_thinking", "data": "xyz"}]


def test_anthropic_style_thinking_blocks_go_back_with_their_tool_calls() -> None:
    gateway = LiteLLMGateway()
    model = "anthropic/claude-sonnet-4-5"
    request = ModelRequest(model=model, messages=_conversation(), thinking_budget_tokens=2048)

    kwargs = gateway._completion_kwargs(model, request)
    translated = AnthropicConfig().transform_request(
        model="claude-sonnet-4-5",
        messages=kwargs["messages"],
        optional_params={"thinking": kwargs["thinking"], "max_tokens": 4096},
        litellm_params={},
        headers={},
    )

    assert "temperature" not in kwargs, "extended thinking takes only the default temperature"
    assert translated.get("thinking"), "thinking is kept for the whole tool loop"
    last_assistant = [m for m in translated["messages"] if m["role"] == "assistant"][-1]
    kinds = [block["type"] for block in last_assistant["content"]]
    assert kinds[0] == "thinking" and kinds[-1] == "tool_use"
    assert last_assistant["content"][0]["signature"] == "sig-abc"


def test_deepseek_gets_reasoning_content_during_the_current_tool_loop_only() -> None:
    model = "deepseek/deepseek-reasoner"
    formatted = LiteLLMGateway()._format_request_messages(_conversation(), model)

    assistants = [m for m in formatted if m["role"] == "assistant"]
    assert "reasoning_content" not in assistants[0], "an earlier turn's reasoning stays out"
    assert assistants[-1]["reasoning_content"] == "I should list it."
    assert all("thinking_blocks" not in m for m in formatted)


def test_deepseek_request_tokens_ignores_historical_reasoning() -> None:
    from dataclasses import replace

    from llm_gateway.tokens import request_tokens

    model = "deepseek/deepseek-reasoner"
    messages = _conversation()
    base_tokens = request_tokens(model, messages)

    bloated = list(_conversation())
    bloated[2] = replace(bloated[2], reasoning="old thought " * 5000)
    bloated_tokens = request_tokens(model, bloated)

    assert base_tokens == bloated_tokens, (
        "earlier turns' reasoning must not inflate DeepSeek token count"
    )


@pytest.mark.parametrize("model", ["openai/gpt-5", "gemini/gemini-2.5-pro", "mistral/large"])
def test_other_families_receive_no_reasoning(model: str) -> None:
    formatted = LiteLLMGateway()._format_request_messages(_conversation(), model)

    assert reasoning_replay(model) is None
    assert all("thinking_blocks" not in m and "reasoning_content" not in m for m in formatted)


def test_temperature_stays_for_requests_without_extended_thinking() -> None:
    gateway = LiteLLMGateway()
    plain = ModelRequest(model="anthropic/claude-sonnet-4-5", messages=_conversation())
    openai = ModelRequest(
        model="openai/o4-mini", messages=_conversation(), thinking_budget_tokens=1
    )

    assert "temperature" in gateway._completion_kwargs(plain.model, plain)
    assert "temperature" in gateway._completion_kwargs(openai.model, openai)


class _ThinkingModel(LLMGatewayProtocol):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        if len(self.requests) == 1:
            yield StreamChunk(
                reasoning_delta="I should ",
                reasoning_blocks=[{"type": "thinking", "thinking": "I should "}],
            )
            yield StreamChunk(
                reasoning_delta="list it.",
                reasoning_blocks=[{"type": "thinking", "thinking": "list it."}],
            )
            yield StreamChunk(reasoning_blocks=[{"type": "thinking", "signature": "sig-abc"}])
            yield StreamChunk(text_delta="Let me look.")
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "c1",
                    "function": {"name": "list_dir", "arguments": '{"path": "."}'},
                }
            )
            return
        yield StreamChunk(text_delta="done")

    async def check_budget(self, *args: Any) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_the_next_step_carries_the_signed_reasoning_of_the_tool_call() -> None:
    model = _ThinkingModel()
    orchestrator = AsyncReActOrchestrator(
        model, ToolRegistry(driver=FakeSandboxDriver(), session_id="s_think")
    )

    async for _ in orchestrator.process_user_turn("s_think", "list the workspace"):
        pass

    caller = [m for m in model.requests[1].messages if m.role == "assistant"][-1]
    assert caller.tool_calls and caller.content == "Let me look."
    assert caller.reasoning == "I should list it."
    assert caller.reasoning_blocks == [
        {"type": "thinking", "thinking": "I should list it.", "signature": "sig-abc"}
    ]
