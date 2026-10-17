"""Requests are laid out so a provider's prompt cache covers the conversation.

Stable prompt first, then the history, then the turn's volatile context. Each step of a turn
repeats the previous request byte for byte up to its last conversation message, which carries a
rolling cache breakpoint for providers with explicit markers (Anthropic-style); providers that
cache prefixes automatically (OpenAI, DeepSeek, Gemini) gain from the stable order alone.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.services.turn_trace import TurnTraceAccumulator
from llm_gateway.gateway import LiteLLMGateway
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import SystemPrompt
from specifications.interfaces.llm import (
    ChatMessage,
    LLMGatewayProtocol,
    ModelRequest,
    StreamChunk,
    ToolDefinition,
)


class _TwoToolsThenAnswer(LLMGatewayProtocol):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        step = len(self.requests)
        if step <= 2:
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"c{step}",
                    "function": {"name": "list_dir", "arguments": '{"path": "."}'},
                }
            )
            return
        yield StreamChunk(text_delta="done")

    async def check_budget(self, *args: Any) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


def _wire(message: ChatMessage) -> tuple[str, str | None, str | None]:
    return (message.role, message.content, json.dumps(message.tool_calls, sort_keys=True))


@pytest.mark.asyncio
async def test_each_step_extends_the_previous_request_up_to_its_breakpoint() -> None:
    gateway = _TwoToolsThenAnswer()
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s_cache")
    orchestrator = AsyncReActOrchestrator(gateway, registry)
    prompt = SystemPrompt(stable="STABLE", dynamic="Active model: x (VOLATILE-123)")

    async for _ in orchestrator.process_user_turn(
        "s_cache", "list things", system_prompt_parts=prompt
    ):
        pass

    assert len(gateway.requests) == 3
    for request in gateway.requests:
        roles = [m.role for m in request.messages]
        assert roles.count("system") == 1 and roles[0] == "system"
        assert request.messages[0].cacheable
        assert "VOLATILE-123" in (request.messages[2].content or ""), (
            "turn-fixed context follows user prompt"
        )
        assert not any(
            "VOLATILE-123" in (m.content or "") for i, m in enumerate(request.messages) if i != 2
        )
    for earlier, later in zip(gateway.requests, gateway.requests[1:], strict=False):
        marked = [i for i, m in enumerate(earlier.messages) if m.cacheable and m.role != "system"]
        assert len(marked) == 1, "one rolling breakpoint per request"
        end = marked[0] + 1
        assert [_wire(m) for m in later.messages[:end]] == [
            _wire(m) for m in earlier.messages[:end]
        ], "the later request repeats the cached prefix byte for byte"


@pytest.mark.parametrize(
    "model, explicit",
    [
        ("anthropic/claude-sonnet-4-5", True),
        ("openrouter/anthropic/claude-sonnet-4.5", True),
        ("openai/gpt-5", False),
        ("deepseek/deepseek-chat", False),
        ("gemini/gemini-2.5-pro", False),
        ("openrouter/deepseek/deepseek-v4.1-flash", False),
    ],
)
def test_explicit_markers_only_for_providers_that_take_them(model: str, explicit: bool) -> None:
    gateway = LiteLLMGateway()
    messages = [
        ChatMessage(role="system", content="S", cacheable=True),
        ChatMessage(role="user", content="question"),
        ChatMessage(role="assistant", tool_calls=[{"id": "c", "function": {"name": "t"}}]),
        ChatMessage(role="tool", content="result", tool_call_id="c", cacheable=True),
        ChatMessage(role="user", content="<system-reminder>ctx</system-reminder>"),
    ]
    tools = [ToolDefinition(name="t", description="d", parameters_json_schema={})]
    request = ModelRequest(model=model, messages=messages, tools=tools)

    kwargs = gateway._completion_kwargs(model, request)
    rendered = json.dumps(kwargs["messages"]) + json.dumps(kwargs["tools"])
    markers = rendered.count("cache_control")

    if explicit:
        assert markers == 3, "stable prompt, tools, rolling breakpoint: within the limit of 4"
        assert "cache_control" not in json.dumps(kwargs["messages"][-1])
    else:
        assert markers == 0


class _Tools:
    def __init__(self, names: list[str]) -> None:
        self.names = names

    def owns(self, name: str) -> bool:
        return name in self.names

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(name=n, description="", parameters_json_schema={}) for n in self.names
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        return ""


def test_external_tools_come_in_a_stable_order() -> None:
    import asyncio

    from agent_core.context import ExecutionContext, current_execution_ctx

    from specifications.interfaces.agent import TurnCallbacks

    registry = ToolRegistry()
    orders = []
    for names in (["mcp__b", "mcp__a", "mcp__c"], ["mcp__c", "mcp__a", "mcp__b"]):
        token = current_execution_ctx.set(
            ExecutionContext(
                session_id="s",
                event_queue=asyncio.Queue(),
                parent_model="m",
                callbacks=TurnCallbacks(external_tools=_Tools(names)),  # type: ignore[arg-type]
            )
        )
        try:
            orders.append([d.name for d in registry.get_tool_definitions()][-3:])
        finally:
            current_execution_ctx.reset(token)

    assert orders[0] == orders[1] == ["mcp__a", "mcp__b", "mcp__c"]


def test_the_trace_reports_the_cache_hit_ratio() -> None:
    trace = TurnTraceAccumulator(session_id="s", org_id="o", turn_index=1)
    trace.record_model_usage({"prompt_tokens": 1000, "cache_read_tokens": 750})
    trace.record_model_usage({"prompt_tokens": 1000, "cache_read_tokens": 850})

    assert trace.finish(status="completed").tokens["usage"]["cache_hit_ratio"] == 0.8
