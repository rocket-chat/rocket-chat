"""A turn's streamed reply sends Slack one step update every couple of seconds at most."""

from __future__ import annotations

import asyncio
from typing import Any

from slack_assistant.stream import MAX_FAILURES, TurnStream


class _Client:
    def __init__(self, failing_appends: int = 0) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.failing_appends = failing_appends

    async def chat_startStream(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("start", kwargs))
        return {"ts": "1.0"}

    async def chat_appendStream(self, **kwargs: Any) -> None:
        self.calls.append(("append", kwargs))
        if self.failing_appends:
            self.failing_appends -= 1
            raise RuntimeError("ratelimited")

    async def chat_stopStream(self, **kwargs: Any) -> None:
        self.calls.append(("stop", kwargs))


def _titles(client: _Client, kind: str) -> list[str]:
    return [c[1]["chunks"][0]["title"] for c in client.calls if c[0] == kind]


async def test_a_burst_of_steps_becomes_one_update_with_the_latest_step() -> None:
    client = _Client()
    stream = TurnStream(
        client, "C", "1.0", recipient_user_id="U", recipient_team_id="T", update_interval=0.05
    )

    for i in range(10):
        await stream.tool_started("bash_exec", {"command": f"step {i}"}, f"Step {i}")
    await asyncio.sleep(0.1)

    assert _titles(client, "start") == ["Step 0"]
    assert _titles(client, "append") == ["Step 9"]


async def test_a_passing_failure_does_not_end_the_stream_but_lasting_ones_do() -> None:
    client = _Client(failing_appends=1)
    stream = TurnStream(
        client, "C", "1.0", recipient_user_id="U", recipient_team_id="T", update_interval=0
    )
    await stream.tool_started("bash_exec", {}, "Start")
    await stream.tool_started("bash_exec", {}, "Fails once")
    await stream.tool_started("bash_exec", {}, "Works again")
    assert stream.started

    client.failing_appends = MAX_FAILURES
    for i in range(MAX_FAILURES):
        await stream.tool_started("bash_exec", {}, f"Fails {i}")
    assert not stream.started
    assert await stream.finish("answer", []) is False, "the answer is posted the usual way"
