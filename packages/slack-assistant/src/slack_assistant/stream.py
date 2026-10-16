"""A turn's reply in its thread, streamed with Slack's agent streaming API.

One message per turn: a single task card shows the step the turn is on (Slack's "thinking
steps"), and when the turn ends it becomes a one-line summary and the answer is written into the
same message. One card rather than one per tool call: an agent can call dozens of tools, and a
card per call buried the answer under a list of finished steps. It replaces a separate progress message: the thread gets one reply, and Slack's status
line under it says the bot is working. The stream opens on the first tool call, so a turn that
only answers posts a plain reply. Any Slack failure turns the stream off and the answer is
posted the usual way; it never fails the turn.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Slack's limits: 256 characters per task chunk, 12,000 per markdown text.
TASK_CHARS = 256
ANSWER_CHARS = 11_500
STEP_CARD = "steps"
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
        self._steps = 0
        self._current = ""
        self._edited: set[str] = set()
        self._commands = 0
        self._reads = 0

    @property
    def started(self) -> bool:
        """The message exists and can still be written to."""
        return self.ts is not None and not self._broken

    async def tool_started(self, name: str, arguments: dict[str, Any], label: str) -> None:
        self._count(name, arguments)
        self._steps += 1
        self._current = label
        await self._task(_card(label, "in_progress"))

    async def finish(
        self, answer: str, blocks: list[dict[str, Any]], *, failed: bool = False
    ) -> bool:
        """Close the step card with the turn's summary and write the answer; False when the
        stream cannot take it (the caller then posts the answer itself)."""
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

    def outcome_line(self) -> str:
        """What the turn did, for the line under its answer: "Done in 42s · Edited 2 files"."""
        elapsed = int(self._clock() - self._started_at)
        return " · ".join(part for part in (f"Done in {elapsed}s", self._summary()) if part)

    async def _task(self, chunk: dict[str, Any]) -> None:
        if self._broken:
            return
        try:
            if self.ts is None:
                response = await self._client.chat_startStream(**self._start_args(), chunks=[chunk])
                self.ts = response.get("ts")
                if self.ts is None:
                    self._broken = True
                return
            await self._client.chat_appendStream(channel=self._channel, ts=self.ts, chunks=[chunk])
        except Exception as err:
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
