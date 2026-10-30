"""Tests for sub-agent parity: empty replies, reasoning replay, and budget inheritance."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.subagents import SubagentConfig, SubagentRunner
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import (
    GenerationParams,
    ReasoningPolicy,
    SubagentTaskRequest,
    TurnPolicy,
)
from specifications.interfaces.llm import LLMGatewayProtocol, ModelRequest, StreamChunk


class MockSubagentGateway(LLMGatewayProtocol):
    def __init__(self, responses: list[list[StreamChunk]]) -> None:
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        chunks = self.responses.pop(0) if self.responses else []
        for chunk in chunks:
            yield chunk

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: object, **kwargs: object) -> None:
        return None


async def test_subagent_continues_on_reasoning_only_reply() -> None:
    # Step 1: reasoning only (finish_reason=length) -> empty reply notice sent
    # Step 2: answers with text
    gateway = MockSubagentGateway(
        [
            [
                StreamChunk(
                    reasoning_delta="Thinking deeply about the task...",
                    finish_reason="length",
                )
            ],
            [
                StreamChunk(
                    text_delta="Here is the final answer.",
                    finish_reason="stop",
                )
            ],
        ]
    )
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s_sub_test")
    runner = SubagentRunner(gateway=gateway, registry=registry)

    req = SubagentTaskRequest(role="researcher", task="Find auth bug")
    result = await runner.run(req, session_id="s_sub_test")

    assert len(gateway.requests) == 3  # the last one asks for the report
    assert "output token limit while you were still reasoning" in (
        gateway.requests[1].messages[-1].content or ""
    )
    assert result.status == "completed"
    assert result.summary == "Here is the final answer."


async def test_subagent_preserves_reasoning_and_thinking_blocks_in_tool_loop() -> None:
    # Step 1: reasoning blocks + tool call
    # Step 2: checks that step 2's assistant message has reasoning_blocks attached
    thinking_block = {
        "type": "thinking",
        "thinking": "analyzing file",
        "signature": "sig_12345",
    }
    gateway = MockSubagentGateway(
        [
            [
                StreamChunk(
                    reasoning_blocks=[thinking_block],
                    tool_call_delta={
                        "index": 0,
                        "id": "call_1",
                        "function": {"name": "list_dir", "arguments": '{"path": "/"}'},
                    },
                )
            ],
            [StreamChunk(text_delta="Analyzed directory.")],
        ]
    )
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s_sub_test")
    runner = SubagentRunner(gateway=gateway, registry=registry)

    req = SubagentTaskRequest(role="researcher", task="List files")
    result = await runner.run(req, session_id="s_sub_test")

    assert len(gateway.requests) == 3  # the last one asks for the report
    # In request 2, find the assistant message: it must have reasoning_blocks
    assistant_msg = next(m for m in gateway.requests[1].messages if m.role == "assistant")
    assert assistant_msg.reasoning_blocks == [thinking_block]
    assert result.status == "completed"


async def test_subagent_inherits_generation_params_from_turn_context() -> None:
    gateway = MockSubagentGateway([[StreamChunk(text_delta="Quick inspection complete.")]])
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s_sub_test")
    runner = SubagentRunner(gateway=gateway, registry=registry)

    policy = TurnPolicy(
        generation=GenerationParams(
            max_tokens=32_000,
            reasoning_effort="high",
            thinking_budget_tokens=8_000,
            fallback_models=("backup-model-1",),
            reasoning=ReasoningPolicy(effort="high", budget_tokens=8_000),
        )
    )
    exec_ctx = ExecutionContext(
        session_id="s_sub_test",
        event_queue=asyncio.Queue(),
        parent_model="claude-3-7-sonnet",
        policy=policy,
    )
    token = current_execution_ctx.set(exec_ctx)
    try:
        req = SubagentTaskRequest(role="researcher", task="Check params")
        await runner.run(req, session_id="s_sub_test")
    finally:
        current_execution_ctx.reset(token)

    assert len(gateway.requests) == 2  # then the report is asked for
    req_sent = gateway.requests[0]
    assert req_sent.max_tokens == 32_000
    assert req_sent.reasoning_effort == "high"
    assert req_sent.thinking_budget_tokens == 8_000
    assert req_sent.fallback_models == ["backup-model-1"]


async def test_subagent_empty_replies_exceeding_retries_returns_partial() -> None:
    gateway = MockSubagentGateway(
        [
            [StreamChunk(finish_reason="length")],
            [StreamChunk(finish_reason="length")],
            [StreamChunk(finish_reason="length")],
        ]
    )
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s_sub_test")
    runner = SubagentRunner(
        gateway=gateway,
        registry=registry,
        configs={
            "tester": SubagentConfig(
                id="tester",
                name="Tester",
                role_title="Tester",
                description="desc",
                system_prompt="prompt",
                whitelisted_tools=[],
                max_turns=10,
            )
        },
    )

    req = SubagentTaskRequest(role="tester", task="Will timeout on length")
    result = await runner.run(req, session_id="s_sub_test")

    assert result.status == "partial"
    assert "kept reaching its output token limit" in result.summary
