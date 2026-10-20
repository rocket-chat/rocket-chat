"""A command's output reaches the cockpit while the command runs, redacted and bounded."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from agent_core.dispatcher import ToolDispatcher
from agent_core.tools import live_output
from agent_core.tools.live_output import LiveOutput, live_chunk
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult
from tests.turn_harness import run_turn


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _collect() -> tuple[list[str], LiveOutput, _Clock]:
    sent: list[str] = []
    clock = _Clock()
    return sent, LiveOutput(sent.append, clock=clock), clock


def test_output_is_batched_until_the_interval_or_size_is_reached() -> None:
    sent, live, clock = _collect()

    live.feed("one\n")
    live.feed("two\n")
    assert sent == []

    clock.now = live_output.FLUSH_INTERVAL_SECONDS
    live.feed("three\n")
    assert sent == ["one\ntwo\nthree\n"]

    live.feed("x" * live_output.FLUSH_CHARS + "\n")
    assert len(sent) == 2


def test_a_line_is_sent_whole_and_the_last_partial_line_on_close() -> None:
    sent, live, clock = _collect()
    clock.now = 1.0

    live.feed("par")
    clock.now = 2.0
    live.feed("tial\nnext")
    assert sent == ["partial\n"]
    live.close()
    assert sent == ["partial\n", "next"]


def test_a_secret_split_across_chunks_is_redacted() -> None:
    sent: list[str] = []
    live = LiveOutput(sent.append, lambda text: text.replace("hunter2", "[REDACTED]"))

    live.feed("token=hun")
    live.feed("ter2\n")
    live.close()

    assert "".join(sent) == "token=[REDACTED]\n"


def test_a_line_that_never_ends_is_still_sent() -> None:
    sent, live, clock = _collect()
    clock.now = 1.0

    live.feed("\r" + "." * (live_output.MAX_PENDING_LINE_CHARS + 10))
    live.close()

    assert sum(len(part) for part in sent) > live_output.MAX_PENDING_LINE_CHARS


def test_output_stops_at_the_ceiling_with_a_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_output, "MAX_STREAMED_CHARS", 50)
    sent, live, clock = _collect()
    clock.now = 1.0

    live.feed("a" * 40 + "\n")
    live.feed("b" * 40 + "\n")
    live.feed("c" * 40 + "\n")
    live.close()

    assert sent[-1].endswith(live_output.TRUNCATION_NOTICE)
    assert "c" * 40 not in "".join(sent)
    assert sum(len(part) for part in sent) < 200


def test_nothing_listens_outside_a_tool_call() -> None:
    assert live_chunk() is None


class _Registry:
    """Prints from the event loop and from a worker thread, as the Kubernetes and Docker drivers do."""

    async def call_tool(self, name: str, args: dict[str, Any]) -> str:
        chunk = live_chunk()
        assert chunk is not None
        chunk("from the loop\n")
        await asyncio.to_thread(chunk, "from a thread\n")
        return "result"


async def test_the_dispatcher_streams_output_ahead_of_the_result() -> None:
    queue: asyncio.Queue[Any] = asyncio.Queue()

    await ToolDispatcher(_Registry()).execute_single_tool(  # type: ignore[arg-type]
        "s1", {"id": "c1", "name": "bash_exec", "arguments": "{}"}, queue
    )

    events = [queue.get_nowait() for _ in range(queue.qsize())]
    types = [event.event_type for event in events]
    assert types[0] == "tool_call"
    assert types[-1] == "tool_completed"
    streamed = [e for e in events if e.event_type == "command_output"]
    assert streamed and all(e.payload["tool_call_id"] == "c1" for e in streamed)
    assert "".join(e.payload["text"] for e in streamed) == "from the loop\nfrom a thread\n"
    assert types.index("command_output") < types.index("tool_completed")


async def test_a_turn_publishes_command_output_once_and_not_again_with_the_result() -> None:
    driver = FakeSandboxDriver()
    driver.script_command("make build", ExecResult(0, "compiling a\ncompiling b\n", "", 0))

    outcome = await run_turn(
        "live_output_turn",
        driver=driver,
        script=[("bash_exec", {"command": "make build", "cwd": "/workspace"})],
    )

    chunks = outcome.events_of("log_chunk")
    shown = "".join(c.payload["text"] for c in chunks)
    assert shown.count("compiling a") == 1
    assert any(c.payload.get("tool_call_id") == "call_1" for c in chunks)
