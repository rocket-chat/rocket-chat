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
