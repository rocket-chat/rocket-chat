"""Live output of a running tool call: what a command prints while it is still running.

Drivers hand a command's output to a callback as it arrives, from a worker thread (Docker) or the
event loop (Kubernetes). ``LiveOutput`` turns those chunks into a few events per second: it
redacts whole lines (a secret split across two chunks would otherwise escape the redactor),
batches them, and stops after a ceiling so a command that prints without end cannot flood the
event bus. The complete output still arrives, clamped, in the tool's result.
"""

from __future__ import annotations

import contextvars
import threading
import time
from collections.abc import Callable

FLUSH_CHARS = 2_000
FLUSH_INTERVAL_SECONDS = 0.1
# A line that never ends (a progress bar redrawing itself) is sent anyway past this length.
MAX_PENDING_LINE_CHARS = 4_000
MAX_STREAMED_CHARS = 200_000
TRUNCATION_NOTICE = "\r\n[live output stopped; the full output is in the result]\r\n"


class LiveOutput:
    """Batches, redacts and caps the chunks of one tool call; safe to feed from any thread."""

    def __init__(
        self,
        emit: Callable[[str], None],
        redact: Callable[[str], str] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._emit = emit
        self._redact = redact
        self._clock = clock
        self._lock = threading.Lock()
        self._ready: list[str] = []
        self._ready_chars = 0
        self._partial = ""
        self._streamed = 0
        self._stopped = False
        self._last_flush = clock()

    def feed(self, chunk: str) -> None:
        """Take a chunk of output as the driver produces it."""
        if not chunk:
            return
        with self._lock:
            if self._stopped:
                return
            text = self._partial + chunk
            lines = text.splitlines(keepends=True)
            self._partial = ""
            if lines and not lines[-1].endswith(("\n", "\r")):
                self._partial = lines.pop()
            if len(self._partial) > MAX_PENDING_LINE_CHARS:
                lines.append(self._partial)
                self._partial = ""
            for line in lines:
                self._ready.append(line)
                self._ready_chars += len(line)
            if self._ready_chars >= FLUSH_CHARS or self._clock() - self._last_flush >= (
                FLUSH_INTERVAL_SECONDS
            ):
                self._flush_locked()

    def close(self) -> None:
        """Send what is left, including a last line without a newline."""
        with self._lock:
            if self._stopped:
                return
            if self._partial:
                self._ready.append(self._partial)
                self._partial = ""
            self._flush_locked()

    def _flush_locked(self) -> None:
        self._last_flush = self._clock()
        if not self._ready:
            return
        text = "".join(self._ready)
        self._ready.clear()
        self._ready_chars = 0
        if self._redact is not None:
            text = self._redact(text)
        room = MAX_STREAMED_CHARS - self._streamed
        if len(text) > room:
            text = text[: max(room, 0)] + TRUNCATION_NOTICE
            self._stopped = True
        self._streamed += len(text)
        self._emit(text)


current_live_output: contextvars.ContextVar[LiveOutput | None] = contextvars.ContextVar(
    "current_live_output", default=None
)


def live_chunk() -> Callable[[str], None] | None:
    """The callback to pass a driver as ``on_stdout_chunk`` for the tool call running now.

    ``None`` outside a tool call (a unit test, a subagent's housekeeping), where nothing is
    listening.
    """
    live = current_live_output.get()
    return live.feed if live is not None else None
