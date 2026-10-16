"""A turn's reply in its thread, streamed with Slack's agent streaming API.

One message per turn: each tool call appears as a task card while it runs (Slack's "thinking
steps", collapsed by default), and the answer is written into the same message when the turn
ends. It replaces a separate progress message: the thread gets one reply, and Slack's status
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
        self._tasks: dict[str, str] = {}
        self._edited: set[str] = set()
        self._commands = 0
        self._reads = 0

    @property
    def started(self) -> bool:
        """The message exists and can still be written to."""
        return self.ts is not None and not self._broken

    async def tool_started(
        self, call_id: str, name: str, arguments: dict[str, Any], label: str
    ) -> None:
        self._count(name, arguments)
        self._tasks[call_id] = label
        await self._task(call_id, label, "in_progress")

    async def tool_completed(self, call_id: str, ok: bool) -> None:
        label = self._tasks.get(call_id)
        if label is not None:
            await self._task(call_id, label, "complete" if ok else "error")

    async def finish(self, answer: str, blocks: list[dict[str, Any]]) -> bool:
        """Write the answer and close the message; False when the stream cannot take it (the
        caller then posts the answer itself)."""
        if not self.started:
            return False
        try:
            await self._client.chat_stopStream(
                channel=self._channel,
                ts=self.ts,
                markdown_text=answer[:ANSWER_CHARS] or "Done.",
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

    async def _task(self, call_id: str, title: str, status: str) -> None:
        chunk = {
            "type": "task_update",
            "id": call_id,
            "title": title[:TASK_CHARS],
            "status": status,
        }
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
