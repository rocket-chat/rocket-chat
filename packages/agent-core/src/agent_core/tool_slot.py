"""The concurrency permits and the time budget of one running tool call.

A tool may wait for a person: ``ask_question``, an approval the permission mode requires, the
approval inside ``apply_diff_file`` or a hard ``git_revert``. That wait can take hours, so it
must neither count against the tool's time budget nor hold a concurrency permit other calls need.
Every human wait goes through :func:`human_wait`, which pauses the slot of the tool call it
happens in: the permits are released and the clock stops until the person has answered.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager


class ToolSlot:
    """Permits (the session's and the process's) and the remaining time of one tool call."""

    def __init__(self, limits: Sequence[asyncio.Semaphore], budget_seconds: float) -> None:
        self.budget_seconds = budget_seconds
        self._limits = tuple(limits)
        self._spent = 0.0
        self._since: float | None = None
        self._waits = 0

    async def acquire(self) -> None:
        """Take every permit, in a fixed order so two slots can never wait on each other."""
        taken: list[asyncio.Semaphore] = []
        try:
            for limit in self._limits:
                await limit.acquire()
                taken.append(limit)
        except BaseException:
            for limit in reversed(taken):
                limit.release()
            raise
        self._since = time.monotonic()

    def release(self) -> None:
        """Give the permits back and stop the clock (idempotent)."""
        if self._since is None:
            return
        self._spent += time.monotonic() - self._since
        self._since = None
        for limit in reversed(self._limits):
            limit.release()

    def remaining(self) -> float:
        running = time.monotonic() - self._since if self._since is not None else 0.0
        return self.budget_seconds - self._spent - running

    @property
    def waiting(self) -> bool:
        """Whether the call is waiting for a person (its clock is stopped)."""
        return self._waits > 0

    @asynccontextmanager
    async def paused(self) -> AsyncIterator[None]:
        self._waits += 1
        if self._waits == 1:
            self.release()
        try:
            yield
        except BaseException:
            # Cancelled or failed while waiting: the call is ending, so the permits are not
            # taken back (waiting for one here could hang the cancellation).
            self._waits -= 1
            raise
        self._waits -= 1
        if self._waits == 0:
            await self.acquire()


current_tool_slot: contextvars.ContextVar[ToolSlot | None] = contextvars.ContextVar(
    "current_tool_slot", default=None
)


@asynccontextmanager
async def human_wait() -> AsyncIterator[None]:
    """Wrap a wait for a person: the enclosing tool call's slot is paused meanwhile."""
    slot = current_tool_slot.get()
    if slot is None:
        yield
        return
    async with slot.paused():
        yield
