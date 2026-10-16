"""A reply with no text and no tool call is not a final answer.

A reasoning model can spend its whole output budget thinking and stop before writing anything.
Taking that empty reply as the answer ended a long coding turn mid-rebase: the platform then
asked the model to close its checklist "and give your final answer", and it aborted its work.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import LLMGatewayProtocol, ModelRequest, StreamChunk

CHECKLIST = json.dumps({"tasks": [{"id": "1", "title": "Rebase onto beta", "status": "in_progress"}]})


class _Scripted(LLMGatewayProtocol):
    """Replies in order: ("call", name, args), ("think", finish_reason), ("say", text)."""

    def __init__(self, script: list[tuple[Any, ...]]) -> None:
        self.script = script
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        step = self.script[min(len(self.requests), len(self.script)) - 1]
        if step[0] == "call":
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"c{len(self.requests)}",
                    "function": {"name": step[1], "arguments": step[2]},
                }
            )
            yield StreamChunk(is_finished=True, finish_reason="tool_calls")
        elif step[0] == "think":
            yield StreamChunk(reasoning_delta="Let me weigh every go.mod conflict first... " * 50)
            yield StreamChunk(is_finished=True, finish_reason=step[1])
        else:
            yield StreamChunk(text_delta=step[1])
            yield StreamChunk(is_finished=True, finish_reason="stop")

    async def check_budget(self, *args: Any) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


def _notes(request: ModelRequest) -> str:
    last = request.messages[-1]
    return last.content or "" if last.role == "user" else ""


async def _run(model: _Scripted, session_id: str) -> AsyncReActOrchestrator:
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id=session_id)
    orchestrator = AsyncReActOrchestrator(model, registry)
    async for _ in orchestrator.process_user_turn(session_id, "rebase the branch"):
        pass
    return orchestrator


async def test_a_reply_cut_while_reasoning_continues_the_turn_instead_of_ending_it() -> None:
    model = _Scripted(
        [
            ("call", "update_task_checklist", CHECKLIST),
            ("think", "length"),
            ("say", "Rebased: 50 conflicts resolved."),
        ]
    )

    orchestrator = await _run(model, "s_empty_cut")

    told = _notes(model.requests[2])
    assert "output token limit while you were still reasoning" in told
    assert "Before you answer" not in told, "an empty reply is not an attempt to finish"
    history = orchestrator._session_histories["s_empty_cut"]
    assert not any(m.role == "assistant" and not (m.content or m.tool_calls) for m in history)
    assert history[-1].content == "Rebased: 50 conflicts resolved."


async def test_an_empty_reply_is_retried_then_explained_never_returned_as_the_answer() -> None:
    model = _Scripted([("think", "stop")])

    orchestrator = await _run(model, "s_empty_always")

    assert len(model.requests) == 3, "two retries, then the turn ends"
    assert "Your last reply was empty" in _notes(model.requests[1])
    answer = orchestrator._session_histories["s_empty_always"][-1].content or ""
    assert answer.startswith("I stopped here: the model returned empty replies")
