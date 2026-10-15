"""Waiting for a person neither times a tool out nor holds a concurrency permit.

``ask_question`` and approvals can wait for hours. Before, the dispatcher's per-tool timeout
(10 minutes) killed them, and each parked call held one of the process-wide tool permits, so a
few sessions waiting for approval stopped every other session's tools.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from agent_core.dispatcher import ToolDispatcher
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tool_slot import ToolSlot, human_wait
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.llm import LLMGatewayProtocol, ModelRequest, StreamChunk


class _Registry:
    """``ask`` waits for a person until released; ``work`` takes ``work_seconds``."""

    def __init__(self, work_seconds: float = 0.0, parked_target: int = 0) -> None:
        self.work_seconds = work_seconds
        self.answer = asyncio.Event()
        self.waiting = 0
        self.parked_target = parked_target
        self.all_parked = asyncio.Event()

    async def call_tool(self, name: str, args: dict[str, Any]) -> str:
        if name == "ask":
            await asyncio.sleep(float(args.get("before", 0)))
            self.waiting += 1
            if self.waiting == self.parked_target:
                self.all_parked.set()
            async with human_wait():
                await self.answer.wait()
            await asyncio.sleep(float(args.get("after", 0)))
            return "answered"
        await asyncio.sleep(self.work_seconds)
        return "worked"


def _call(name: str, **args: Any) -> dict[int, dict[str, Any]]:
    import json

    return {0: {"id": f"c_{name}", "name": name, "arguments": json.dumps(args)}}


async def _run(dispatcher: ToolDispatcher, session: str, call: dict[int, dict[str, Any]]) -> str:
    messages: list[Any] = []
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
    await dispatcher.execute_tool_calls(session, call, queue, messages)
    return str(messages[-1].content)


async def test_time_spent_waiting_for_a_person_is_not_working_time() -> None:
    registry = _Registry()
    dispatcher = ToolDispatcher(registry, tool_timeout_seconds=1)  # type: ignore[arg-type]

    running = asyncio.create_task(_run(dispatcher, "s", _call("ask", before=0.3, after=0.3)))
    await asyncio.sleep(2.5)
    registry.answer.set()

    assert await running == "answered"


async def test_working_time_is_still_bounded() -> None:
    dispatcher = ToolDispatcher(_Registry(work_seconds=5), tool_timeout_seconds=1)  # type: ignore[arg-type]

    started = time.monotonic()
    result = await _run(dispatcher, "s", _call("work"))

    assert result.startswith("Tool execution timed out") and time.monotonic() - started < 3


async def test_sessions_parked_on_a_person_do_not_block_other_sessions() -> None:
    registry = _Registry(work_seconds=0.01, parked_target=8)
    dispatcher = ToolDispatcher(  # type: ignore[arg-type]
        registry, max_concurrency=1, process_concurrency=2, tool_timeout_seconds=30
    )

    parked = [asyncio.create_task(_run(dispatcher, f"waiting_{n}", _call("ask"))) for n in range(8)]
    await asyncio.wait_for(registry.all_parked.wait(), timeout=5)
    other = await asyncio.wait_for(_run(dispatcher, "busy", _call("work")), timeout=2)

    assert other == "worked"
    registry.answer.set()
    assert await asyncio.gather(*parked) == ["answered"] * 8


async def test_one_session_cannot_take_every_process_permit() -> None:
    registry = _Registry(work_seconds=0.3)
    dispatcher = ToolDispatcher(  # type: ignore[arg-type]
        registry, max_concurrency=2, process_concurrency=3, tool_timeout_seconds=30
    )
    many = {index: {"id": f"c{index}", "name": "work", "arguments": "{}"} for index in range(6)}

    greedy = asyncio.create_task(_run(dispatcher, "greedy", many))
    await asyncio.sleep(0.05)
    started = time.monotonic()
    await _run(dispatcher, "polite", _call("work"))

    assert time.monotonic() - started < 0.5, "a permit was left for another session"
    await greedy


async def test_a_paused_slot_gives_its_permits_back_and_takes_them_again() -> None:
    limit = asyncio.Semaphore(1)
    slot = ToolSlot([limit], budget_seconds=10)
    await slot.acquire()
    assert limit.locked()

    async with slot.paused():
        assert not limit.locked() and slot.waiting
        spent_before = slot.remaining()
        await asyncio.sleep(0.2)
        assert slot.remaining() == spent_before, "the clock is stopped"

    assert limit.locked() and not slot.waiting
    slot.release()
    assert not limit.locked()


class _AskingModel(LLMGatewayProtocol):
    """Asks one question, then answers with whatever the tool returned."""

    def __init__(self) -> None:
        self.asked = False

    async def chat_stream(self, request: ModelRequest) -> Any:
        if not self.asked:
            self.asked = True
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "q1",
                    "function": {
                        "name": "ask_question",
                        "arguments": '{"question": "Which?", "options": ["a", "b"]}',
                    },
                }
            )
            return
        result = [m.content for m in request.messages if m.role == "tool"][-1]
        yield StreamChunk(text_delta=f"Got: {result}")

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: object, **kwargs: object) -> None:
        return None


def _orchestrator(session: str) -> AsyncReActOrchestrator:
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id=session)
    orchestrator = AsyncReActOrchestrator(_AskingModel(), registry)
    orchestrator.dispatcher.tool_timeout_seconds = 1
    return orchestrator


async def test_a_question_answered_after_the_tool_timeout_reaches_the_turn() -> None:
    orchestrator = _orchestrator("s_slow_answer")
    answers: list[str] = []
    background: list[asyncio.Task[None]] = []

    async for event in orchestrator.process_user_turn("s_slow_answer", "go"):
        if event.event_type == "interactive_question":
            question_id = event.payload["question_id"]

            async def answer_later(qid: str = question_id) -> None:
                await asyncio.sleep(2.5)
                await orchestrator.submit_question_answer("s_slow_answer", qid, ["b"])

            background.append(asyncio.create_task(answer_later()))
        if event.event_type == "thought" and not event.payload.get("is_reasoning"):
            answers.append(event.payload["delta"])

    await asyncio.gather(*background)
    assert "User response: b" in "".join(answers)
    assert not orchestrator._pending_questions and not orchestrator._question_answers


@pytest.mark.parametrize("delay", [0.1, 1.5])
async def test_cancelling_a_parked_turn_forgets_its_question(delay: float) -> None:
    orchestrator = _orchestrator(f"s_cancel_{delay}")
    session = f"s_cancel_{delay}"
    background: list[asyncio.Task[None]] = []

    async for event in orchestrator.process_user_turn(session, "go"):
        if event.event_type == "interactive_question":

            async def cancel_later() -> None:
                await asyncio.sleep(delay)
                await orchestrator.cancel_turn(session, propagate=False)

            background.append(asyncio.create_task(cancel_later()))

    await asyncio.gather(*background)
    assert not orchestrator._pending_questions and not orchestrator._question_answers
    assert orchestrator.get_session_state(session) in {"cancelled", "completed"}


async def test_a_parked_question_releases_the_hosts_turn_slot_while_it_waits() -> None:
    from contextlib import asynccontextmanager

    orchestrator = _orchestrator("s_scope")
    suspended: list[bool] = []

    @asynccontextmanager
    async def turn_slot_released() -> Any:
        suspended.append(True)
        try:
            yield
        finally:
            suspended.append(False)

    orchestrator.human_wait_scope = turn_slot_released
    async for event in orchestrator.process_user_turn("s_scope", "go"):
        if event.event_type == "interactive_question":
            await asyncio.sleep(0.05)
            assert suspended == [True], "the slot is released while the question waits"
            await orchestrator.submit_question_answer(
                "s_scope", event.payload["question_id"], ["a"]
            )

    assert suspended == [True, False]
