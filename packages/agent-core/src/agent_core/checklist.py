"""Keeping the agent's task checklist honest.

The checklist is the user's view of progress, and models often stop updating it or leave it
half done. The latest ``update_task_checklist`` call in the conversation is the source of truth
(the tool replaces the whole list), so the engine reads it from there and, before it accepts a
final answer, asks the model once to bring the list up to date.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from specifications.interfaces.llm import ChatMessage

TOOL_NAME = "update_task_checklist"
OPEN_STATUSES = frozenset({"pending", "in_progress"})
STALE_AFTER_STEPS = 20


def latest_tasks(conversation: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """The tasks of the most recent checklist update, or an empty list."""
    for message in reversed(conversation):
        for call in reversed(message.tool_calls or []):
            name = call.get("name")
            args = call.get("arguments")
            if not name and isinstance(call.get("function"), dict):
                name = call["function"].get("name")
                args = call["function"].get("arguments")
            if name != TOOL_NAME:
                continue
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except (ValueError, AttributeError):
                    return []
            if isinstance(args, dict):
                tasks = args.get("tasks", [])
                if isinstance(tasks, list):
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
        f"Reminder: you have worked {since} steps without updating your task checklist. If a "
        "task has finished or a new one started, call `update_task_checklist` with the complete "
        "list; otherwise carry on. Never re-send an unchanged list."
    )


EDIT_TOOLS = frozenset(
    {"file_write", "file_edit", "file_replace", "apply_patch", "apply_diff_file", "replace_symbol"}
)
VERIFY_TOOLS = frozenset({"run_tests", "lint", "typecheck", "build"})
# Shell commands that test, build or check a project: as good a verification as the tools.
_VERIFY_COMMAND = re.compile(
    r"\b(?:go\s+(?:test|build|vet)|golangci-lint|make|mage|pytest|tox|nox|ruff|mypy|tsc|"
    r"eslint|jest|vitest|playwright|cargo\s+(?:test|build|check|clippy)|gradle\w*|mvn\w*|"
    r"dotnet\s+(?:test|build)|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|build|lint|"
    r"typecheck|type-check|check))\b"
)


def _call_name_and_arguments(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw_function = call.get("function")
    function: dict[str, Any] = raw_function if isinstance(raw_function, dict) else {}
    name = call.get("name") or function.get("name", "")
    raw = call.get("arguments", function.get("arguments"))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    return str(name), raw if isinstance(raw, dict) else {}


def _verifies(name: str, arguments: dict[str, Any]) -> bool:
    if name in VERIFY_TOOLS:
        return True
    if name in ("bash_exec", "process_start"):
        return bool(_VERIFY_COMMAND.search(str(arguments.get("command", ""))))
    # Waiting for CI or reading a pull request's checks is verification by the project's own CI.
    if name == "wait_for":
        return arguments.get("target") == "ci_status"
    return name == "github_pull_request" and arguments.get("action") == "checks"


def verification_gate_notice(conversation: Sequence[ChatMessage]) -> str | None:
    """A reminder when this turn edited code and nothing verified it before completing.

    Only the current turn counts (back to the latest user message): an edit an earlier turn
    already verified, or a turn woken only to read CI results, must not send the agent
    rebuilding a project it has finished with.
    """
    for message in reversed(conversation):
        if message.role == "user":
            return None
        for call in message.tool_calls or []:
            name, arguments = _call_name_and_arguments(call)
            if _verifies(name, arguments):
                return None
            if name in EDIT_TOOLS:
                if not any(t.get("status") == "completed" for t in latest_tasks(conversation)):
                    return None
                return (
                    "Verification gate: you changed files in this turn and nothing has checked "
                    "them since (tests, a build, a linter or CI). If the project can be checked "
                    "here, run that check now. If you already verified another way, or it cannot "
                    "be checked in this sandbox, do not try further: give your final answer and "
                    "say in one line what was and was not verified."
                )
    return None
