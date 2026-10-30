"""What a running child tells the client: its start and end, its steps nested under the parent's
call, its streamed text merged into a few events, and its model calls for the turn's totals.

A foreground child writes to its turn's event queue. A background child may outlive its turn,
so it writes to the session's sink instead (``TurnCallbacks.subagent_event_sink``), which
publishes straight to the session's event bus.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from specifications.interfaces.agent import AgentEvent

# Streamed text goes out at most every 250 ms or 200 characters: one event per token made a
# busy child flood the event bus and every open tab.
PROGRESS_INTERVAL_SECONDS = 0.25
PROGRESS_CHARS = 200
# The agent events the turn handler publishes as ``tool_started`` / ``tool_completed`` with the
# child's id, without recording them as the parent's own steps.
CHILD_TOOL_STARTED = "subagent_tool_call"
CHILD_TOOL_COMPLETED = "subagent_tool_completed"
# Arguments and results of a child's step as the client sees them (the child's transcript has
# them whole).
CHILD_RESULT_CHARS = 4_000

Sink = Callable[[AgentEvent], Awaitable[None]]


class SubagentEvents:
    """One child's events, with its id and the parent call it nests under on each."""

    def __init__(
        self,
        *,
        session_id: str,
        subagent_id: str,
        parent_tool_call_id: str | None,
        queue: asyncio.Queue[AgentEvent | None] | None,
        sink: Sink | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_id = session_id
        self.subagent_id = subagent_id
        self.parent_tool_call_id = parent_tool_call_id
        self._queue = queue
        self._sink = sink
        self._clock = clock
        self._buffer: list[str] = []
        self._buffered_chars = 0
        self._buffer_reasoning = False
        self._first_buffered_at = 0.0

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        event = AgentEvent(event_type=event_type, session_id=self._session_id, payload=payload)
        if self._sink is not None:
            await self._sink(event)
        elif self._queue is not None:
            await self._queue.put(event)

    def _identity(self) -> dict[str, Any]:
        return {"subagent_id": self.subagent_id, "parent_tool_call_id": self.parent_tool_call_id}

    async def progress(self, delta: str, *, is_reasoning: bool) -> None:
        if not delta:
            return
        if self._buffer and is_reasoning != self._buffer_reasoning:
            await self.flush()
        if not self._buffer:
            self._first_buffered_at = self._clock()
            self._buffer_reasoning = is_reasoning
        self._buffer.append(delta)
        self._buffered_chars += len(delta)
        waited = self._clock() - self._first_buffered_at
        if self._buffered_chars >= PROGRESS_CHARS or waited >= PROGRESS_INTERVAL_SECONDS:
            await self.flush()

    async def flush(self) -> None:
        if not self._buffer:
            return
        text, self._buffer, self._buffered_chars = "".join(self._buffer), [], 0
        await self.emit(
            "subagent_progress",
            {
                "subagent_id": self.subagent_id,
                "delta": text,
                "is_reasoning": self._buffer_reasoning,
            },
        )

    async def tool_started(self, call_id: str, name: str, arguments: str) -> None:
        await self.flush()
        await self.emit(
            CHILD_TOOL_STARTED,
            {"id": call_id, "name": name, "arguments": arguments, **self._identity()},
        )

    async def tool_completed(
        self, call_id: str, name: str, arguments: str, result: str, status: str, duration_ms: int
    ) -> None:
        shown = (
            result
            if len(result) <= CHILD_RESULT_CHARS
            else (
                f"{result[:CHILD_RESULT_CHARS]}\n[... {len(result) - CHILD_RESULT_CHARS} characters "
                "cut; the whole result is in the subagent's transcript]"
            )
        )
        await self.emit(
            CHILD_TOOL_COMPLETED,
            {
                "id": call_id,
                "name": name,
                "arguments": arguments,
                "result": shown,
                "status": status,
                "duration_ms": duration_ms,
                **self._identity(),
            },
        )

    async def model_usage(self, payload: dict[str, Any]) -> None:
        """A child's model call, folded into the turn's token and cost totals (never published)."""
        if self._sink is None:
            await self.emit("model_usage", {**payload, "role": "subagent", **self._identity()})
