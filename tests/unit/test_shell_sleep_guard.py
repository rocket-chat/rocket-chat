"""A long ``sleep`` in the shell is refused: waiting belongs to ``wait_for`` or the PR watch."""

from __future__ import annotations

import pytest
from agent_core.tools.builtin.commands import long_sleep_in

from tests.turn_harness import run_turn


@pytest.mark.parametrize(
    ("command", "seconds"),
    [
        ("sleep 200; echo waited", 200),
        ("sleep 60", 60),
        ("make build && sleep 120 && gh pr checks", 120),
        ("for i in 1 2 3; do sleep 45; done", 45),
        ("sleep 5 && curl -s localhost:3000", None),
        ("npm start & sleep 10; curl localhost", None),
        ("echo 'sleep 300'", None),
        ("go test -run TestSleep60 ./...", None),
    ],
)
def test_only_a_sleep_long_enough_to_be_a_wait_is_caught(command: str, seconds: int | None) -> None:
    assert long_sleep_in(command) == seconds


async def test_a_long_sleep_is_not_run_and_the_agent_is_told_how_to_wait() -> None:
    # slack_2ff369f1_…: the agent had no wait_for and slept 200 s waiting for a PR's CI.
    outcome = await run_turn(
        "sleep_guard",
        script=[("bash_exec", {"command": "sleep 200; echo waited", "cwd": "/workspace"})],
    )
    assert not any("sleep 200" in command for _, command in outcome.driver.exec_calls)
    assert "wakes it by itself when its CI finishes" in outcome.text_of_events()
