"""Keeping the agent's task checklist honest.

The checklist is the user's view of progress, and models often stop updating it or leave it
half done. The latest ``update_task_checklist`` call in the conversation is the source of truth
(the tool replaces the whole list), so the engine reads it from there and, before it accepts a
final answer, asks the model once to bring the list up to date.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from specifications.interfaces.llm import ChatMessage

TOOL_NAME = "update_task_checklist"
OPEN_STATUSES = frozenset({"pending", "in_progress"})
STALE_AFTER_STEPS = 8


def latest_tasks(conversation: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """The tasks of the most recent checklist update, or an empty list."""
    for message in reversed(conversation):
        for call in reversed(message.tool_calls or []):
            function = call.get("function", {})
            if function.get("name") != TOOL_NAME:
                continue
            try:
                tasks = json.loads(function.get("arguments") or "{}").get("tasks", [])
            except (ValueError, AttributeError):
                return []
            return [task for task in tasks if isinstance(task, dict)]
    return []


def unfinished_notice(conversation: Sequence[ChatMessage]) -> str | None:
    """A reminder naming the open tasks, or ``None`` when there is nothing to reconcile."""
    open_tasks = [t for t in latest_tasks(conversation) if t.get("status") in OPEN_STATUSES]
    if not open_tasks:
        return None
    listed = "\n".join(
        f"- [{t.get('status')}] {t.get('title', t.get('id', '?'))}" for t in open_tasks
    )
    return (
        "Before you answer: your task checklist still has open tasks:\n"
        f"{listed}\n"
        "Call `update_task_checklist` now with the complete list: mark what you finished as "
        "completed, mark what you did not do or abandoned as failed (or drop it if it no "
        "longer applies), and leave nothing pending or in progress. Then give your final answer."
    )


def stale_notice(conversation: Sequence[ChatMessage]) -> str | None:
    """A reminder when work has gone on for a while without the checklist being touched.

    Fires on every ``STALE_AFTER_STEPS``-th assistant step after the last update, so a model that
    ignores it hears it again later, but never on each step.
    """
    if not any(t.get("status") in OPEN_STATUSES for t in latest_tasks(conversation)):
        return None
    since = 0
    for message in reversed(conversation):
        if any(c.get("function", {}).get("name") == TOOL_NAME for c in message.tool_calls or []):
            break
        if message.role == "assistant":
            since += 1
    if since == 0 or since % STALE_AFTER_STEPS:
        return None
    return (
        f"Reminder: you have worked {since} steps without updating your task checklist. Call "
        "`update_task_checklist` with the complete list (finished tasks completed, the current "
        "one in_progress), together with your next tool call."
    )
