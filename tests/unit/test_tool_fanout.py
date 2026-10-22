"""Unit tests for bounded tool fan-out and per-tool timeouts.

The dispatcher is driven through a fake registry so concurrency and timeouts are
observed directly, without a sandbox or a real tool.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from agent_core.dispatcher import ToolDispatcher

from specifications.interfaces.agent import AgentEvent


class _FakeRegistry:
    """Records peak concurrency and can simulate a hang."""

    def __init__(self, delay: float = 0.0, hang: bool = False) -> None:
        self.delay = delay
        self.hang = hang
        self.active = 0
        self.max_active = 0

    async def call_tool(self, name: str, args: dict[str, Any]) -> str:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.hang:
                await asyncio.Event().wait()
            elif self.delay:
                await asyncio.sleep(self.delay)
            return f"{name} ok"
        finally:
            self.active -= 1


def _tool_calls(count: int) -> dict[int, dict[str, Any]]:
    return {
        index: {"id": f"call_{index}", "name": "file_read", "arguments": "{}"}
        for index in range(count)
    }


async def _drain(queue: asyncio.Queue[AgentEvent | None]) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    while not queue.empty():
        event = queue.get_nowait()
        if event is not None:
            events.append(event)
    return events


async def test_fan_out_never_exceeds_the_concurrency_bound() -> None:
    registry = _FakeRegistry(delay=0.02)
    dispatcher = ToolDispatcher(registry, max_concurrency=2, tool_timeout_seconds=5)  # type: ignore[arg-type]
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    await dispatcher.execute_tool_calls("s", _tool_calls(6), queue, [])

    assert registry.max_active <= 2


async def test_per_tool_timeout_is_recorded_in_the_completed_event() -> None:
    registry = _FakeRegistry(hang=True)
    dispatcher = ToolDispatcher(registry, tool_timeout_seconds=1)  # type: ignore[arg-type]
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    await dispatcher.execute_tool_calls("s", _tool_calls(1), queue, [])

    completed = [e for e in await _drain(queue) if e.event_type == "tool_completed"]
    assert len(completed) == 1
    payload = completed[0].payload
    assert payload["status"] == "timeout"
    assert payload["timed_out"] is True
    assert payload["timeout_ms"] == 1000


async def test_a_failing_tool_is_tagged_error_not_timeout() -> None:
    class _BoomRegistry:
        async def call_tool(self, name: str, args: dict[str, Any]) -> str:
            raise RuntimeError("permission denied")

    dispatcher = ToolDispatcher(_BoomRegistry(), tool_timeout_seconds=5)  # type: ignore[arg-type]
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    await dispatcher.execute_tool_calls("s", _tool_calls(1), queue, [])

    completed = [e for e in await _drain(queue) if e.event_type == "tool_completed"]
    assert completed[0].payload["status"] == "error"
    assert completed[0].payload["timed_out"] is False


@pytest.mark.parametrize("count", [1, 3])
async def test_every_call_yields_a_result(count: int) -> None:
    registry = _FakeRegistry()
    dispatcher = ToolDispatcher(registry, max_concurrency=4, tool_timeout_seconds=5)  # type: ignore[arg-type]
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    await dispatcher.execute_tool_calls("s", _tool_calls(count), queue, [])

    completed = [e for e in await _drain(queue) if e.event_type == "tool_completed"]
    assert len(completed) == count


async def test_unreadable_arguments_come_back_with_the_tools_own_parameters() -> None:
    class _Registry(_FakeRegistry):
        def argument_hint(self, name: str) -> str:
            return "Its parameters: searchText (string, required), start (string)"

    dispatcher = ToolDispatcher(_Registry(), tool_timeout_seconds=5)  # type: ignore[arg-type]
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
    broken = {
        0: {"id": "call_0", "name": "search_logs", "arguments": '{"searchText": "x", "start": '}
    }

    await dispatcher.execute_tool_calls("s", broken, queue, [])

    completed = [e for e in await _drain(queue) if e.event_type == "tool_completed"]
    result = str(completed[0].payload["result"])
    assert completed[0].payload["status"] == "error"
    assert "not valid JSON" in result and "one complete JSON object" in result
    assert "searchText (string, required)" in result


def test_a_tools_parameters_fit_on_one_short_line() -> None:
    from agent_core.tools.registry import ToolRegistry

    hint = ToolRegistry().argument_hint("file_read")

    assert hint.startswith("Its parameters:") and len(hint) <= 500
    assert ToolRegistry().argument_hint("no_such_tool") == ""
