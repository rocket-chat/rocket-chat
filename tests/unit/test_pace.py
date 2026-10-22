"""Reminders about the pace of a turn: too long without a change, and one read per step."""

from __future__ import annotations

import json
from typing import Any

from agent_core.pace import (
    BATCH_REMINDER_EVERY,
    ENVIRONMENT_REMINDER_EVERY,
    RESEARCH_REMINDER_EVERY,
    pace_notice,
)

from specifications.interfaces.llm import ChatMessage
from tests.turn_harness import run_turn


def _step(*names: str, call_id: int = 0, arguments: str = "{}") -> list[ChatMessage]:
    calls = [
        {
            "id": f"c{call_id}-{i}",
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }
        for i, name in enumerate(names)
    ]
    results = [
        ChatMessage(role="tool", content="ok", tool_call_id=f"c{call_id}-{i}")
        for i in range(len(names))
    ]
    return [ChatMessage(role="assistant", content=None, tool_calls=calls), *results]


def _turn(*steps: tuple[str, ...]) -> list[ChatMessage]:
    messages = [ChatMessage(role="user", content="Implement it.")]
    for index, step in enumerate(steps):
        messages += _step(*step, call_id=index)
    return messages


def test_a_turn_that_researches_for_ten_steps_without_a_change_is_asked_to_decide() -> None:
    steps = [("bash_exec",)] * RESEARCH_REMINDER_EVERY

    notice = pace_notice(_turn(*steps))

    assert notice is not None and "without changing anything" in notice
    assert "build and test it here in the sandbox" in notice, "building locally is encouraged"
    assert pace_notice(_turn(*steps[:-1])) is None
    assert pace_notice(_turn(*steps, ("bash_exec",))) is None, "not on every step"
    assert pace_notice(_turn(*steps, *steps)) is not None, "and again later"


def test_a_change_in_the_turn_or_a_read_only_mode_silences_the_research_reminder() -> None:
    steps = [("bash_exec",)] * (RESEARCH_REMINDER_EVERY - 1) + [("file_edit",)]

    assert pace_notice(_turn(*steps)) is None
    assert pace_notice(_turn(*[("bash_exec",)] * RESEARCH_REMINDER_EVERY), may_edit=False) is None


def test_only_this_turns_steps_count() -> None:
    earlier = _turn(*[("bash_exec",)] * RESEARCH_REMINDER_EVERY)
    now = [*earlier, ChatMessage(role="user", content="Another thing."), *_step("bash_exec")]

    assert pace_notice(now) is None


def test_reading_one_thing_per_step_is_asked_to_be_batched() -> None:
    reads = [("file_read",)] * BATCH_REMINDER_EVERY

    notice = pace_notice(_turn(*reads))

    assert notice is not None and "ONE step" in notice
    assert pace_notice(_turn(*reads[:-1])) is None
    assert pace_notice(_turn(*reads, ("grep_search",))) is None, "not on every step"
    assert pace_notice(_turn(*reads, *reads)) is not None, "and again later"


def test_a_step_that_already_batches_or_runs_a_command_breaks_the_streak() -> None:
    assert (
        pace_notice(
            _turn(("file_read",), ("file_read",), ("file_read", "file_read"), ("file_read",))
        )
        is None
    )
    assert (
        pace_notice(_turn(("file_read",), ("file_read",), ("file_read",), ("bash_exec",))) is None
    )


async def test_the_reminders_reach_the_model_at_the_end_of_the_request() -> None:
    reads: list[tuple[str, dict[str, Any]]] = [
        ("file_read", {"path": f"/workspace/f{i}.go"}) for i in range(RESEARCH_REMINDER_EVERY + 1)
    ]

    outcome = await run_turn("org_pace", script=reads)

    requests = [r for r in outcome.model.requests if r.tools]
    tail = [(r.messages[-1].content or "") for r in requests]
    assert "ONE step" not in tail[BATCH_REMINDER_EVERY - 1]
    assert "ONE step" in tail[BATCH_REMINDER_EVERY], "after four single reads"
    assert "without changing anything" not in tail[RESEARCH_REMINDER_EVERY - 1]
    assert "without changing anything" in tail[RESEARCH_REMINDER_EVERY], "after ten steps"


def _reading(path: str, count: int) -> list[ChatMessage]:
    messages = [ChatMessage(role="user", content="Implement it.")]
    for index in range(count):
        messages += _step(
            "file_read", "grep_search", call_id=index, arguments=f'{{"path": "{path}"}}'
        )
    return messages


def test_reading_dependency_source_again_and_again_is_asked_to_stop() -> None:
    path = "/root/go/pkg/mod/go.mongodb.org/mongo-driver@v1/bson/decoder.go"

    notice = pace_notice(_reading(path, 2))

    assert notice is not None and "third-party dependencies" in notice
    assert "build it here in the sandbox" in notice
    assert pace_notice(_reading(path, 1)) is None, "two reads are under the first multiple"
    assert pace_notice(_reading("/work/app/main.go", 6)) is None, "the project's own code is free"


def test_every_package_manager_cache_counts_as_dependency_source() -> None:
    for path in (
        "/app/node_modules/react/index.js",
        "/usr/lib/python3.12/site-packages/httpx/_client.py",
        "/root/.cargo/registry/src/serde/lib.rs",
    ):
        assert pace_notice(_reading(path, 2)) is not None, path


def test_the_reminder_about_dependency_source_gets_firmer_when_it_was_ignored() -> None:
    path = "/root/go/pkg/mod/go.mongodb.org/mongo-driver@v1/bson/decoder.go"

    first = pace_notice(_reading(path, 2))
    later = pace_notice(_reading(path, 3))

    assert first is not None and "Read the source only for one specific behaviour" in first
    assert later is not None and "Do not read any more of it" in later


def _results(contents: list[str]) -> list[ChatMessage]:
    messages = [ChatMessage(role="user", content="Publish it.")]
    for index, content in enumerate(contents):
        call = {
            "id": f"c{index}",
            "type": "function",
            "function": {"name": "bash_exec", "arguments": "{}"},
        }
        messages += [
            ChatMessage(role="assistant", content=None, tool_calls=[call]),
            ChatMessage(role="tool", content=content, tool_call_id=f"c{index}"),
        ]
    return messages


def test_a_turn_that_keeps_failing_on_its_environment_is_asked_to_hand_back() -> None:
    refused = "fatal: Invalid or expired git gateway credential"
    steps = [refused] * ENVIRONMENT_REMINDER_EVERY

    notice = pace_notice(_results(steps))

    assert notice is not None and "failed on the environment" in notice
    assert "ask_question" in notice
    assert pace_notice(_results(steps[:-1])) is None, "five failures are not yet a pattern"
    assert pace_notice(_results([*steps, refused])) is None, "not on every step"
    assert pace_notice(_results([*steps, *steps])) is not None, "and again if it goes on"


def test_ordinary_failures_do_not_count_as_the_environment() -> None:
    assert pace_notice(_results(["FAIL: TestFoo expected 3, got 4"] * 12)) is None


def test_working_around_the_git_gateway_is_called_out_once() -> None:
    def step(command: str) -> list[ChatMessage]:
        return _step("bash_exec", call_id=7, arguments=json.dumps({"command": command}))

    turn = [ChatMessage(role="user", content="Publish it.")]
    plain = turn + step("git status")
    assert pace_notice(plain) is None

    workaround = turn + step(
        "git config --local url./workspace/fake-origin/.insteadOf https://github.com/x"
    )
    notice = pace_notice(workaround)
    assert notice is not None and "do not work around a refused one" in notice
    assert pace_notice(workaround + step("git config --local --get-regexp insteadof")) is None, (
        "said once is enough"
    )
