"""A turn's reply in its thread, streamed with Slack's agent streaming API.

One message per turn: a single task card shows the step the turn is on (Slack's "thinking
steps"), and when the turn ends it becomes a one-line summary and the answer is written into the
same message. One card rather than one per tool call: an agent can call dozens of tools, and a
card per call buried the answer under a list of finished steps. It replaces a separate progress message: the thread gets one reply, and Slack's status
line under it says the bot is working. The stream opens on the first tool call, so a turn that
only answers posts a plain reply. Step updates are merged (one every ``UPDATE_INTERVAL_SECONDS``
at most): an agent can call several tools a second, and Slack rate-limits ``appendStream``.
A failure is retried by the client's handlers; failures that last turn the stream off and the
answer is posted the usual way; it never fails the turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Slack's limits: 256 characters per task chunk, 12,000 per markdown text.
TASK_CHARS = 256
ANSWER_CHARS = 11_500
STEP_CARD = "steps"
# Slack allows about one chat.appendStream per second per message; stay well under it.
UPDATE_INTERVAL_SECONDS = 2.0
# Consecutive failed updates after which the stream is given up (the answer is posted plainly).
MAX_FAILURES = 3
# Checklist items shown as task items; a longer checklist is cut (the web app shows it whole).
MAX_PLAN_ITEMS = 8
_PLAN_STATUS = {
    "pending": "pending",
    "in_progress": "in_progress",
    "completed": "complete",
    "failed": "error",
}
_READ_TOOLS = frozenset({"file_read", "list_dir", "glob_files", "code_search", "grep_search"})
_EDIT_TOOLS = frozenset({"file_edit", "file_write", "apply_patch", "apply_diff_file"})


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


class TurnStream:
    def __init__(
        self,
        client: Any,
        channel_id: str,
        thread_ts: str,
        *,
        recipient_user_id: str | None,
        recipient_team_id: str | None,
        clock: Callable[[], float] = time.monotonic,
        update_interval: float | None = None,
    ) -> None:
        self._client = client
        self._channel = channel_id
        self._thread_ts = thread_ts
        self._recipient_user = recipient_user_id
        self._recipient_team = recipient_team_id
        self._clock = clock
        self._started_at = clock()
        self.ts: str | None = None
        self._broken = False
        self._failures = 0
        self._update_interval = (
            UPDATE_INTERVAL_SECONDS if update_interval is None else update_interval
        )
        self._last_update = float("-inf")
        self._pending: dict[str, Any] | None = None
        self._flush: asyncio.Task[None] | None = None
        self._steps = 0
        self._current = ""
        self._edited: set[str] = set()
        self._commands = 0
        self._reads = 0
        self._plan: tuple[tuple[str, str], ...] = ()

    @property
    def started(self) -> bool:
        """The message exists and can still be written to."""
        return self.ts is not None and not self._broken

    async def tool_started(self, name: str, arguments: dict[str, Any], label: str) -> None:
        self._count(name, arguments)
        self._steps += 1
        self._current = label
        card = _card(label, "in_progress")
        wait = self._last_update + self._update_interval - self._clock()
        if self.ts is None or wait <= 0:
            await self._task(card)
            return
        # Too soon after the last update: the latest step goes out once the interval is over.
        self._pending = card
        if self._flush is None or self._flush.done():
            self._flush = asyncio.create_task(self._flush_after(wait))

    async def _flush_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        card, self._pending = self._pending, None
        if card is not None:
            await self._task(card)

    async def finish(
        self, answer: str, blocks: list[dict[str, Any]], *, failed: bool = False
    ) -> bool:
        """Close the step card with the turn's summary and write the answer; False when the
        stream cannot take it (the caller then posts the answer itself)."""
        if self._flush is not None:
            self._flush.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush
            self._flush, self._pending = None, None
        if not self.started:
            return False
        # A stopped turn's card keeps the step it stopped on; the answer says who stopped it.
        if failed:
            card = _card(self._current or "Stopped", "error")
        else:
            card = _card(f"{self.outcome_line()} · {_plural(self._steps, 'step')}", "complete")
        chunks = [
            card,
            {"type": "markdown_text", "text": answer[:ANSWER_CHARS] or "Done."},
        ]
        try:
            await self._client.chat_stopStream(
                channel=self._channel,
                ts=self.ts,
                chunks=chunks,
                **({"blocks": blocks} if blocks else {}),
            )
        except Exception as err:
            logger.info("Could not finish the streamed reply: %s", err)
            self._broken = True
            return False
        self.ts = None
        return True

    async def plan(self, tasks: list[dict[str, Any]]) -> None:
        """The agent's checklist as task items, each updated in place by its position. Sent
        only when it changed (the agent re-sends it often), and only once the stream exists:
        a checklist alone does not open a reply."""
        items = tuple(
            (
                str(task.get("title") or task.get("content") or "").strip(),
                _PLAN_STATUS.get(str(task.get("status") or "pending"), "pending"),
            )
            for task in tasks[:MAX_PLAN_ITEMS]
            if isinstance(task, dict)
        )
        if not items or items == self._plan or self.ts is None:
            return
        self._plan = items
        for index, (title, status) in enumerate(items):
            await self._task(
                {
                    "type": "task_update",
                    "id": f"plan-{index}",
                    "title": (title or f"Step {index + 1}")[:TASK_CHARS],
                    "status": status,
                }
            )

    async def subagent(self, subagent_id: str, title: str, status: str) -> None:
        """One line per subagent, its own card updated in place: started, then done with a
        one-line summary and its cost. Rare enough (two updates a child) to skip the merge."""
        await self._task(
            {
                "type": "task_update",
                "id": f"subagent-{subagent_id}"[:64],
                "title": title[:TASK_CHARS],
                "status": status,
            }
        )

    @property
    def elapsed_seconds(self) -> int:
        return int(self._clock() - self._started_at)

    def changes_line(self) -> str:
        """What the turn changed and ran: "Edited 2 files · Ran 3 commands"."""
        return self._summary()

    def outcome_line(self) -> str:
        """What the turn did, for the line under its answer: "Done in 42s · Edited 2 files"."""
        elapsed = int(self._clock() - self._started_at)
        return " · ".join(part for part in (f"Done in {elapsed}s", self._summary()) if part)

    async def _task(self, chunk: dict[str, Any]) -> None:
        if self._broken:
            return
        self._last_update = self._clock()
        try:
            if self.ts is None:
                response = await self._client.chat_startStream(**self._start_args(), chunks=[chunk])
                self.ts = response.get("ts")
                if self.ts is None:
                    self._broken = True
                return
            await self._client.chat_appendStream(channel=self._channel, ts=self.ts, chunks=[chunk])
            self._failures = 0
        except Exception as err:
            # A step update that fails is skipped; the next one carries the latest step anyway.
            # A stream that cannot start, or keeps failing, is given up.
            self._failures += 1
            if self.ts is None or self._failures >= MAX_FAILURES:
                logger.info("Streamed reply unavailable, answering without it: %s", err)
                self._broken = True

    def _start_args(self) -> dict[str, Any]:
        args: dict[str, Any] = {
            "channel": self._channel,
            "thread_ts": self._thread_ts,
            "task_display_mode": "timeline",
        }
        # Required in channels: the stream is rendered for the person who asked.
        if self._recipient_user and self._recipient_team:
            args["recipient_user_id"] = self._recipient_user
            args["recipient_team_id"] = self._recipient_team
        return args

    def _count(self, name: str, arguments: dict[str, Any]) -> None:
        if name in _EDIT_TOOLS:
            self._edited.add(str(arguments.get("path") or name))
        elif name == "bash_exec":
            self._commands += 1
        elif name in _READ_TOOLS:
            self._reads += 1

    def _summary(self) -> str:
        parts = []
        if self._edited:
            parts.append(f"Edited {_plural(len(self._edited), 'file')}")
        if self._commands:
            parts.append(f"Ran {_plural(self._commands, 'command')}")
        if self._reads:
            parts.append(f"Read {_plural(self._reads, 'file')}")
        return " · ".join(parts)


def _card(title: str, status: str) -> dict[str, Any]:
    """The turn's one step card; the same id updates its title and status in place. No
    ``details``: Slack appends each update's details instead of replacing them."""
    return {"type": "task_update", "id": STEP_CARD, "title": title[:TASK_CHARS], "status": status}
