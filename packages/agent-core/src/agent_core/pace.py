"""Reminders about the pace of a turn: researching for too long, and reading one file at a time.

An agent given a task it could start at once can spend most of its time reasoning and reading, one
call per step, before it changes anything: each step costs a model round trip with the whole
context, and long reasoning costs minutes. The prompt asks for fast decisions and for independent
calls in ONE step; models still drift, so the engine says so when the conversation shows it.

A third drift is reading the source of the libraries a task uses, file after file, to confirm what
their documentation already says.

Like the checklist reminder they are derived from the conversation (nothing is stored), fire on
every Nth step rather than on each, and are rendered at the end of a request only.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_core.checklist import EDIT_TOOLS
from agent_core.context_policy import find_turn_user_index
from specifications.interfaces.llm import ChatMessage

# Steps of a turn without a change before the agent is asked to decide, and again every so often.
RESEARCH_REMINDER_EVERY = 10
# Consecutive steps of a single read before the agent is asked to batch, and again every so often.
BATCH_REMINDER_EVERY = 4
READ_TOOLS = frozenset({"file_read", "grep_search", "glob_files", "list_dir", "code_search"})
# Where package managers keep the source of third-party dependencies.
DEPENDENCY_SOURCE_MARKERS = (
    "/pkg/mod/",
    "node_modules/",
    "site-packages/",
    ".cargo/registry/",
    ".m2/repository/",
    ".gradle/caches/",
)
# Calls into that source, over the turn, before the agent is asked to stop, and again every so often.
DEPENDENCY_REMINDER_EVERY = 3


def _calls(message: ChatMessage) -> list[str]:
    return [str((c.get("function") or {}).get("name") or "") for c in message.tool_calls or []]


def _dependency_reads(message: ChatMessage) -> int:
    """How many of the step's calls look into a dependency's source."""
    return sum(
        any(
            marker in str((call.get("function") or {}).get("arguments") or "")
            for marker in DEPENDENCY_SOURCE_MARKERS
        )
        for call in message.tool_calls or []
    )


def _dependency_notice(assistant_steps: Sequence[ChatMessage]) -> str | None:
    """A reminder when the latest step took the turn's reads of dependency source to a new multiple."""
    before = sum(_dependency_reads(m) for m in assistant_steps[:-1])
    total = before + _dependency_reads(assistant_steps[-1]) if assistant_steps else 0
    if total // DEPENDENCY_REMINDER_EVERY == before // DEPENDENCY_REMINDER_EVERY:
        return None
    return (
        f"You have looked into the source of third-party dependencies {total} times this turn. "
        "A library's documented behaviour does not need its source to confirm: write the code "
        "that uses it as documented, build it here in the sandbox, and let the compiler and a "
        "test show what differs. Read the source only for one specific behaviour a test just "
        "showed you does not match."
    )


def pace_notice(conversation: Sequence[ChatMessage], *, may_edit: bool = True) -> str | None:
    """A reminder when this turn has researched for long without changing anything, or has read
    files one per step for a while; ``None`` otherwise. ``may_edit`` is false in a read-only mode,
    where not changing anything is the point."""
    start = find_turn_user_index(conversation)
    assistant_steps = [
        m for m in conversation[start + 1 :] if m.role == "assistant" and m.tool_calls
    ]
    steps = [_calls(m) for m in assistant_steps]
    notes: list[str] = []
    if dependency := _dependency_notice(assistant_steps):
        notes.append(dependency)

    if may_edit and steps and len(steps) % RESEARCH_REMINDER_EVERY == 0:
        if not any(name in EDIT_TOOLS for step in steps for name in step):
            notes.append(
                f"You have spent {len(steps)} steps in this turn without changing anything. Stop "
                "researching: state your decision in one line, then act. Implement the simplest "
                "approach that meets the goal, then build and test it here in the sandbox and let "
                "the compiler and the tests show what is wrong. Do not read a library's source "
                "to confirm a behaviour it documents: assume the documented behaviour and check "
                "it with a test. If the task needs no change, answer now."
            )

    streak = 0
    for step in reversed(steps):
        if len(step) != 1 or step[0] not in READ_TOOLS:
            break
        streak += 1
    if streak and streak % BATCH_REMINDER_EVERY == 0:
        notes.append(
            f"Your last {streak} steps each read one thing. Read every file you will need, and "
            "run independent searches and commands, together in ONE step: parallel calls cost no "
            "extra round trip."
        )
    return "\n\n".join(notes) or None
