"""The agent is asked, once, to settle its checklist before the engine accepts a final answer."""

from __future__ import annotations

import json
from typing import Any

from agent_core.checklist import STALE_AFTER_STEPS, latest_tasks, stale_notice, unfinished_notice
from turn_harness import run_turn

from specifications.interfaces.llm import ChatMessage

OPEN = [
    {"id": "1", "title": "Write the fix", "status": "completed"},
    {"id": "2", "title": "Run the tests", "status": "in_progress"},
]


def _reminders(requests: list[Any]) -> int:
    return sum(
        1
        for request in requests
        for message in request.messages[-1:]
        if message.role == "user"
        and "<system-reminder>" in str(message.content)
        and "task checklist still has open tasks" in str(message.content)
    )


def _agent_steps(requests: list[Any]) -> int:
    """Model calls made by the ReAct loop (side tasks such as titles carry no tools)."""
    return sum(1 for request in requests if request.tools)


def _update(tasks: list[dict[str, str]]) -> ChatMessage:
    return ChatMessage(
        role="assistant",
        tool_calls=[
            {
                "id": "c1",
                "type": "function",
                "function": {
                    "name": "update_task_checklist",
                    "arguments": json.dumps({"tasks": tasks}),
                },
            }
        ],
    )


def test_the_latest_update_is_the_checklist_and_open_tasks_are_named() -> None:
    older = _update([{"id": "1", "title": "Old", "status": "pending"}])
    conversation = [older, ChatMessage(role="tool", content="ok", tool_call_id="c1"), _update(OPEN)]

    assert [t["id"] for t in latest_tasks(conversation)] == ["1", "2"]
    notice = unfinished_notice(conversation)
    assert notice is not None and "Run the tests" in notice and "Write the fix" not in notice


def test_a_finished_or_absent_checklist_needs_no_reminder() -> None:
    done = [
        {"id": "1", "title": "x", "status": "completed"},
        {"id": "2", "title": "y", "status": "failed"},
    ]

    assert unfinished_notice([_update(done)]) is None
    assert unfinished_notice([ChatMessage(role="user", content="hi")]) is None


async def test_the_model_is_asked_once_to_settle_an_unfinished_checklist() -> None:
    outcome = await run_turn(
        "org_checklist_open", script=[("update_task_checklist", {"tasks": OPEN})]
    )

    assert _reminders(outcome.model.requests) == 1
    assert _agent_steps(outcome.model.requests) == 3, (
        "plan, premature answer, answer after the reminder"
    )


async def test_a_settled_checklist_is_not_questioned() -> None:
    settled = [{"id": "1", "title": "Write the fix", "status": "completed"}]

    outcome = await run_turn(
        "org_checklist_done", script=[("update_task_checklist", {"tasks": settled})]
    )

    assert _reminders(outcome.model.requests) == 0
    assert _agent_steps(outcome.model.requests) == 2


def _steps(count: int) -> list[ChatMessage]:
    return [ChatMessage(role="assistant", content="working") for _ in range(count)]


def test_a_checklist_left_untouched_for_a_while_is_flagged_periodically() -> None:
    base = [_update(OPEN)]

    assert stale_notice(base + _steps(STALE_AFTER_STEPS - 1)) is None
    assert f"{STALE_AFTER_STEPS} steps" in (stale_notice(base + _steps(STALE_AFTER_STEPS)) or "")
    assert stale_notice(base + _steps(STALE_AFTER_STEPS + 1)) is None
    assert stale_notice(base + _steps(2 * STALE_AFTER_STEPS)) is not None


def test_no_stale_reminder_without_open_tasks_or_after_an_update() -> None:
    done = [{"id": "1", "title": "x", "status": "completed"}]

    assert stale_notice([_update(done), *_steps(STALE_AFTER_STEPS)]) is None
    assert stale_notice(_steps(STALE_AFTER_STEPS)) is None
    assert stale_notice([_update(OPEN), *_steps(STALE_AFTER_STEPS), _update(OPEN)]) is None
