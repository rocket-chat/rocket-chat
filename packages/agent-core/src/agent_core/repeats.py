"""Noticing an agent that sends the same call again and again.

The error text of a rejected call is usually clear, yet some models resend it unchanged many
times. The call still runs (a retry can legitimately succeed), but once it has failed twice with
identical arguments the result carries an explicit instruction to change course.

The same happens with calls that succeed: a model that has lost track of its progress re-reads
the same pull request or re-runs the same command dozens of times. Those still run too (state
may have changed), but a result identical to the previous identical calls of the turn says so,
so the model can move on instead of starting the step over. Nothing here blocks or pauses a
turn: long autonomous sessions must keep running.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from agent_core.context_policy import find_turn_user_index
from specifications.interfaces.llm import ChatMessage

REPEAT_AFTER_FAILURES = 2
REPEAT_AFTER_UNCHANGED = 2
_ERROR_MARKERS = ("error", "failed", "blocked", "refused", "invalid", "not found")
_UNCHANGED_MARKER = "\n\n[Repeated call:"
# Polling a running process or task legitimately returns the same output until it changes.
_POLLING_TOOLS = frozenset(
    {"process_output", "process_wait", "process_list", "task_status", "task_list", "wait_for"}
)


def looks_like_failure(output: str) -> bool:
    head = output[:200].lower()
    return any(marker in head for marker in _ERROR_MARKERS)


def _canonical(arguments: str) -> str:
    """Arguments with keys sorted: models resend the same call with its keys reordered."""
    try:
        return json.dumps(json.loads(arguments), sort_keys=True)
    except (TypeError, ValueError):
        return arguments


def _same_calls(messages: Sequence[ChatMessage], name: str, arguments: str) -> list[str]:
    """Ids of the earlier calls of ``name`` with these arguments."""
    wanted = _canonical(arguments)
    return [
        str(call.get("id"))
        for message in messages
        for call in message.tool_calls or []
        if call.get("function", {}).get("name") == name
        and _canonical(str(call.get("function", {}).get("arguments", ""))) == wanted
    ]


def repeated_failure_note(
    messages: Sequence[ChatMessage], name: str, arguments: str, output: str
) -> str | None:
    """A warning for ``output`` when the identical call already failed twice before, else ``None``."""
    if not looks_like_failure(output):
        return None
    results = {m.tool_call_id: m.content or "" for m in messages if m.role == "tool"}
    failures = sum(
        1
        for call_id in _same_calls(messages, name, arguments)
        if looks_like_failure(results.get(call_id, ""))
    )
    if failures < REPEAT_AFTER_FAILURES:
        return None
    return (
        f"[Repeated failure: this exact `{name}` call has now failed {failures + 1} times. "
        "Do not send it again. Read the error, change the arguments, or use another approach; "
        "if you cannot, tell the user what is blocking you.]"
    )


def unchanged_repeat_note(
    messages: Sequence[ChatMessage], name: str, arguments: str, output: str
) -> str | None:
    """A notice when this turn already made the identical call twice and got the same result."""
    if name in _POLLING_TOOLS or looks_like_failure(output):
        return None
    turn = messages[max(find_turn_user_index(messages), 0) :]
    results = {
        m.tool_call_id: (m.content or "").split(_UNCHANGED_MARKER, 1)[0]
        for m in turn
        if m.role == "tool"
    }
    unchanged = sum(
        1 for call_id in _same_calls(turn, name, arguments) if results.get(call_id) == output
    )
    if unchanged < REPEAT_AFTER_UNCHANGED:
        return None
    return (
        f"{_UNCHANGED_MARKER.strip()} this exact `{name}` call returned the same result "
        f"{unchanged + 1} times in this turn, so nothing it reports has changed. Use the result "
        "you already have and continue with the next step of your plan; to wait for something "
        "external (CI, a review, a process), use `wait_for` instead of polling.]"
    )


def repeat_note(
    messages: Sequence[ChatMessage], name: str, arguments: str, output: str
) -> str | None:
    """The notice a call's result carries when it repeats an earlier identical call, if any."""
    return repeated_failure_note(messages, name, arguments, output) or unchanged_repeat_note(
        messages, name, arguments, output
    )
