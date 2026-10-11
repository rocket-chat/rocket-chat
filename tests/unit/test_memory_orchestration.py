"""Unit tests for memory tools, registry wiring and dynamic-suffix injection.

The critical invariant under test is prompt caching: recalled memories are volatile, so
they may only appear in the *dynamic* system message after the cache breakpoint. The
byte-stable prefix must be identical across turns whether or not a session has memories.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry

from specifications.interfaces.agent import SystemPrompt
from specifications.interfaces.llm import LLMGatewayProtocol, ModelRequest, StreamChunk

STABLE_PREFIX = "You are a Senior Autonomous Software Engineer."
MEMORY_BLOCK = (
    "# --- Recalled Long-Term Memory (volatile; not cacheable) ---\n"
    "- [fact/org] Production deploys run at 09:00 UTC."
)


class CapturingGateway(LLMGatewayProtocol):
    """Records every dispatched request and answers with a single text chunk."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        yield StreamChunk(text_delta="acknowledged")

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        return None


async def _drain(orchestrator: AsyncReActOrchestrator, session_id: str, prompt: str) -> None:
    parts = SystemPrompt(stable=STABLE_PREFIX, dynamic="")
    async for _ in orchestrator.process_user_turn(session_id, prompt, system_prompt_parts=parts):
        pass


def _memory_message(request: ModelRequest) -> Any:
    return next((message for message in request.messages if message.content == MEMORY_BLOCK), None)


@pytest.mark.asyncio
async def test_memory_goes_in_dynamic_suffix_and_prefix_stays_byte_stable() -> None:
    gateway = CapturingGateway()
    registry = ToolRegistry()

    async def remember_cb(content: str, kind: str, scope: str) -> str:
        return "stored"

    async def recall_cb(query: str, scope: str | None, limit: int) -> str:
        return MEMORY_BLOCK

    registry.set_memory_callbacks(remember=remember_cb, recall=recall_cb)
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)

    await _drain(orchestrator, "sess_mem", "When do deploys run?")

    async def quiet_recall(query: str, scope: str | None, limit: int) -> str:
        return ""

    registry.set_memory_callbacks(remember=remember_cb, recall=quiet_recall)
    await _drain(orchestrator, "sess_mem", "Thanks, and what about rollbacks?")

    first = gateway.requests[0]
    last = gateway.requests[-1]

    # Invariant 1: the cacheable prefix is byte-identical across turns, with and without memories.
    assert first.messages[0].content == STABLE_PREFIX
    assert last.messages[0].content == STABLE_PREFIX
    assert first.messages[0].cacheable is True
    assert last.messages[0].cacheable is True

    # The memory is present in the first turn, in a non-cacheable message, and absent later.
    memory_message = _memory_message(first)
    assert memory_message is not None
    assert memory_message.cacheable is False
    assert memory_message.role == "system"
    assert _memory_message(last) is None


@pytest.mark.asyncio
async def test_recall_tool_is_registered_and_delegates() -> None:
    registry = ToolRegistry()
    seen: dict[str, Any] = {}

    async def remember_cb(content: str, kind: str, scope: str) -> str:
        seen["remember"] = (content, kind, scope)
        return "stored"

    async def recall_cb(query: str, scope: str | None, limit: int) -> str:
        seen["recall"] = (query, scope, limit)
        return "remembered deploy window"

    registry.set_memory_callbacks(remember=remember_cb, recall=recall_cb)

    names = {definition.name for definition in registry.get_tool_definitions()}
    assert {"remember", "recall"} <= names

    recalled = await registry.call_tool("recall", {"query": "deploys", "limit": 3})
    assert recalled == "remembered deploy window"
    assert seen["recall"] == ("deploys", None, 3)

    remembered = await registry.call_tool(
        "remember", {"content": "Deploys run at 09:00", "kind": "decision", "scope": "org"}
    )
    assert remembered == "stored"
    assert seen["remember"] == ("Deploys run at 09:00", "decision", "org")


@pytest.mark.asyncio
async def test_memory_tools_degrade_when_unbound() -> None:
    registry = ToolRegistry()

    recalled = await registry.call_tool("recall", {"query": "anything"})
    remembered = await registry.call_tool("remember", {"content": "anything"})

    assert "not available" in recalled
    assert "not available" in remembered
    assert await registry.build_memory_context("anything") == ""


@pytest.mark.asyncio
async def test_recall_injection_failure_is_swallowed() -> None:
    registry = ToolRegistry()

    async def broken_recall(query: str, scope: str | None, limit: int) -> str:
        raise RuntimeError("database unavailable")

    async def remember_cb(content: str, kind: str, scope: str) -> str:
        return "stored"

    registry.set_memory_callbacks(remember=remember_cb, recall=broken_recall)

    # A recall outage degrades to "no memories" rather than failing the turn.
    assert await registry.build_memory_context("query") == ""
