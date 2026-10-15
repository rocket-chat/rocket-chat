"""Unit tests for turn-budget handling and the forced final synthesis.

A tool-happy model can exhaust the ReAct budget without ever answering. The orchestrator
must then force a plain-text completion (``tool_choice="none"``), retry once, and — if the
model still refuses to answer — fall back to a deterministic digest so the turn never ends
with a blank response. It must also nudge the model to converge before the hard cap.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import LLMGatewayProtocol, ModelRequest, StreamChunk

_TOOL_CALL = {
    "index": 0,
    "id": "call_loop",
    "type": "function",
    "function": {"name": "list_dir", "arguments": '{"path": "."}'},
}


class _ToolLoopGateway(LLMGatewayProtocol):
    """Always returns a tool call on react steps; configurable on the forced answer."""

    def __init__(self, answer_on_synthesis: bool) -> None:
        self.answer_on_synthesis = answer_on_synthesis
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        if request.tool_choice == "none":
            if self.answer_on_synthesis:
                yield StreamChunk(text_delta="FINAL ANSWER")
            else:
                # A badly-behaved model that calls a tool even when told not to.
                yield StreamChunk(tool_call_delta=_TOOL_CALL)
            yield StreamChunk(is_finished=True, finish_reason="stop")
            return
        yield StreamChunk(tool_call_delta=_TOOL_CALL)
        yield StreamChunk(is_finished=True, finish_reason="tool_calls")

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


def _orchestrator(gateway: _ToolLoopGateway, session_id: str) -> AsyncReActOrchestrator:
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id=session_id)
    return AsyncReActOrchestrator(gateway=gateway, registry=registry)


async def _run(orchestrator: AsyncReActOrchestrator, session_id: str, limit: int) -> list[str]:
    event_types: list[str] = []
    async for event in orchestrator.process_user_turn(session_id, "do the task", max_turns=limit):
        event_types.append(event.event_type)
    return event_types


def _last_assistant(orchestrator: AsyncReActOrchestrator, session_id: str) -> str:
    history = orchestrator._session_histories[session_id]
    assistants = [m.content for m in history if m.role == "assistant" and m.content]
    return assistants[-1] if assistants else ""


@pytest.mark.asyncio
async def test_exhausted_budget_forces_a_final_answer() -> None:
    gateway = _ToolLoopGateway(answer_on_synthesis=True)
    orchestrator = _orchestrator(gateway, "sess_budget_answer")

    events = await _run(orchestrator, "sess_budget_answer", limit=3)

    assert "post_failure_synthesis" in events
    # The terminal synthesis call is explicitly forced to text.
    assert any(request.tool_choice == "none" for request in gateway.requests)
    assert _last_assistant(orchestrator, "sess_budget_answer") == "FINAL ANSWER"


@pytest.mark.asyncio
async def test_empty_synthesis_falls_back_deterministically() -> None:
    gateway = _ToolLoopGateway(answer_on_synthesis=False)
    orchestrator = _orchestrator(gateway, "sess_budget_fallback")

    await _run(orchestrator, "sess_budget_fallback", limit=3)

    answer = _last_assistant(orchestrator, "sess_budget_fallback")
    # Never blank: the deterministic fallback names the limit and carries gathered context.
    assert answer
    assert "step limit" in answer


@pytest.mark.asyncio
async def test_soft_budget_notice_is_injected_before_the_cap() -> None:
    gateway = _ToolLoopGateway(answer_on_synthesis=True)
    orchestrator = _orchestrator(gateway, "sess_budget_notice")

    await _run(orchestrator, "sess_budget_notice", limit=5)

    trailing = [request.messages[-1] for request in gateway.requests]
    assert any(
        m.role == "user"
        and "<system-reminder>" in (m.content or "")
        and "Budget notice" in m.content
        for m in trailing
    ), "the notice rides at the end of the request"
    assert not any(
        "Budget notice" in (m.content or "")
        for request in gateway.requests
        for m in request.messages
        if m.role == "system"
    ), "never as a system message"
