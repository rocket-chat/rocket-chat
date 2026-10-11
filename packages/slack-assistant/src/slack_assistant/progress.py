"""One live progress message per turn: an activity summary, the current step and a Stop button.

Slack's status loader only shows a single line and disappears; a message that is updated in
place gives the thread a lasting record of what the turn did, and a Stop button to end it.
Updates are throttled (Slack rate-limits ``chat.update``), and any failure turns the message
off for the rest of the turn instead of breaking it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from slack_assistant.block_kit import build_progress_blocks

logger = logging.getLogger(__name__)

MAX_RECENT_STEPS = 4
MAX_FAILURES = 3
_READ_TOOLS = frozenset({"file_read", "list_dir", "glob_files", "code_search", "grep_search"})
_EDIT_TOOLS = frozenset({"file_edit", "file_write", "apply_patch", "apply_diff_file"})


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


class ProgressMessage:
    def __init__(
        self,
        client: Any,
        channel_id: str,
        thread_ts: str,
        *,
        session_id: str,
        link_url: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        min_interval_seconds: float = 1.5,
    ) -> None:
        self._client = client
        self._channel = channel_id
        self._thread_ts = thread_ts
        self._session_id = session_id
        self._link = link_url
        self._clock = clock
        self._interval = min_interval_seconds
        self._started_at = clock()
        self._last_flush = float("-inf")
        self._failures = 0
        self.ts: str | None = None
        self._current = "Starting…"
        self._recent: list[str] = []
        self._edited: set[str] = set()
        self._commands = 0
        self._reads = 0

    @property
    def active(self) -> bool:
        return self.ts is not None and self._failures < MAX_FAILURES

    async def start(self) -> None:
        try:
            response = await self._client.chat_postMessage(
                channel=self._channel,
                thread_ts=self._thread_ts,
                text="Working on it…",
                blocks=self._blocks(stop=True),
            )
            self.ts = response.get("ts")
            self._last_flush = self._clock()
        except Exception as err:
            logger.info("Live progress message unavailable: %s", err)
            self._failures = MAX_FAILURES

    async def tool_started(self, name: str, arguments: dict[str, Any], label: str) -> None:
        if name in _EDIT_TOOLS:
            self._edited.add(str(arguments.get("path") or name))
        elif name == "bash_exec":
            self._commands += 1
        elif name in _READ_TOOLS:
            self._reads += 1
        self._current = label
        self._remember(label)
        await self._flush()

    async def note(self, message: str) -> None:
        self._current = message
        await self._flush()

    async def finish(self, *, ok: bool, error: str | None = None) -> None:
        if not self.active:
            return
        elapsed = int(self._clock() - self._started_at)
        if ok:
            headline = f":white_check_mark: *Done* in {elapsed}s"
        elif error:
            headline = f":warning: *Stopped* after {elapsed}s: {error[:200]}"
        else:
            headline = f":stop_button: *Stopped* after {elapsed}s"
        summary = self._summary()
        await self._update(headline, [summary] if summary else [], stop=False)

    def _summary(self) -> str:
        parts = []
        if self._edited:
            parts.append(f"Edited {_plural(len(self._edited), 'file')}")
        if self._commands:
            parts.append(f"Ran {_plural(self._commands, 'command')}")
        if self._reads:
            parts.append(f"Read {_plural(self._reads, 'file')}")
        return " · ".join(parts)

    def _remember(self, label: str) -> None:
        if not self._recent or self._recent[-1] != label:
            self._recent.append(label)
            del self._recent[:-MAX_RECENT_STEPS]

    def _blocks(self, *, stop: bool, headline: str | None = None) -> list[dict[str, Any]]:
        steps = [f"• {step}" for step in self._recent[:-1]]
        summary = self._summary()
        if summary:
            steps.append(summary)
        return build_progress_blocks(
            headline=headline or f":hourglass_flowing_sand: {self._current}",
            steps=steps,
            stop_value=self._session_id if stop else None,
            link_url=self._link,
        )

    async def _flush(self) -> None:
        now = self._clock()
        if not self.active or now - self._last_flush < self._interval:
            return
        await self._update(f":hourglass_flowing_sand: {self._current}", None, stop=True)

    async def _update(self, headline: str, steps: list[str] | None, *, stop: bool) -> None:
        if not self.active:
            return
        blocks = (
            self._blocks(stop=stop, headline=headline)
            if steps is None
            else build_progress_blocks(
                headline=headline,
                steps=steps,
                stop_value=self._session_id if stop else None,
                link_url=self._link,
            )
        )
        try:
            await self._client.chat_update(
                channel=self._channel, ts=self.ts, text=headline, blocks=blocks
            )
            self._last_flush = self._clock()
        except Exception as err:
            self._failures += 1
            logger.debug("Progress update failed (%d/%d): %s", self._failures, MAX_FAILURES, err)
