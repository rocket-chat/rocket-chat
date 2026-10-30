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
import re
from collections.abc import Sequence
from typing import Any

from agent_core.checklist import _VERIFY_COMMAND, EDIT_TOOLS, VERIFY_TOOLS
from agent_core.context_policy import find_turn_user_index
from specifications.interfaces.llm import ChatMessage

REPEAT_AFTER_FAILURES = 2
REPEAT_AFTER_UNCHANGED = 2
_ERROR_MARKERS = ("error", "failed", "blocked", "refused", "invalid", "not found")
_UNCHANGED_MARKER = "\n\n[Repeated call:"
# Polling a running process or task legitimately returns the same output until it changes.
_POLLING_TOOLS = frozenset({"process_output", "process_wait", "process_list", "wait_for"})
# Strips timing and volatile progress percentages from test/build error tails
_STRIP_VOLATILE = re.compile(
    r"\s*(?:\bin\s+\d+(?:\.\d+)?(?:s|ms|m\s+\d+s)\b|\[\s*\d+%\s*\]|\b\d+(?:\.\d+)?s\b)",
    re.IGNORECASE,
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


def _is_verify_call(name: str, arguments: str) -> bool:
    """True when the call runs test, lint, typecheck, or build verification."""
    if name in VERIFY_TOOLS:
        return True
    if name == "bash_exec":
        try:
            cmd = json.loads(arguments).get("command", "") if isinstance(arguments, str) else ""
            return bool(_VERIFY_COMMAND.search(cmd))
        except (TypeError, ValueError):
            return bool(_VERIFY_COMMAND.search(str(arguments)))
    return False


def _failure_digest(output: str) -> str:
    """Normalized tail signature of a verification failure to detect stagnant errors."""
    cleaned = output.split("\n\n[Stagnant failure:", 1)[0].split("\n\n[Repeated failure:", 1)[0]
    lines = [
        _STRIP_VOLATILE.sub("", line).strip()
        for line in cleaned.strip().splitlines()
        if line.strip()
    ]
    # Use the last 12 non-empty lines where tracebacks and failure summaries concentrate
    tail = lines[-12:] if len(lines) > 12 else lines
    return "\n".join(tail)


def _call_info(call: dict[str, Any]) -> tuple[str, str, str]:
    """Returns (call_id, tool_name, canonical_arguments)."""
    call_id = str(call.get("id", ""))
    raw_func = call.get("function")
    function: dict[str, Any] = raw_func if isinstance(raw_func, dict) else {}
    name = str(call.get("name") or function.get("name", ""))
    raw_args = str(call.get("arguments") or function.get("arguments", ""))
    return call_id, name, _canonical(raw_args)


def _history_events(
    messages: Sequence[ChatMessage],
) -> list[tuple[str, str, str, str, bool]]:
    """Chronological list of (call_id, name, canonical_args, result, is_mutation)."""
    results = {m.tool_call_id: m.content or "" for m in messages if m.role == "tool"}
    events: list[tuple[str, str, str, str, bool]] = []
    for message in messages:
        for call in message.tool_calls or []:
            cid, cname, cargs = _call_info(call)
            res = results.get(cid, "")
            is_mutation = cname in EDIT_TOOLS or (
                cname == "bash_exec" and "[workspace changes:" in res
            )
            events.append((cid, cname, cargs, res, is_mutation))
    return events


def _same_calls(messages: Sequence[ChatMessage], name: str, arguments: str) -> list[str]:
    """Ids of the earlier calls of ``name`` with these arguments."""
    wanted = _canonical(arguments)
    return [
        cid
        for cid, cname, cargs, _, _ in _history_events(messages)
        if cname == name and cargs == wanted
    ]


def _verification_stagnation_note(
    events: list[tuple[str, str, str, str, bool]],
    name: str,
    arguments: str,
    current_output: str,
) -> str | None:
    """Flags when consecutive edit-verify cycles keep failing with the identical error."""
    wanted = _canonical(arguments)
    current_digest = _failure_digest(current_output)
    stagnant_count = 0
    for _, cname, cargs, res, _ in events:
        if cname == name and cargs == wanted:
            if looks_like_failure(res) and _failure_digest(res) == current_digest:
                stagnant_count += 1
            else:
                stagnant_count = 0
    if stagnant_count < REPEAT_AFTER_FAILURES:
        return None
    return (
        f"[Stagnant failure: your last {stagnant_count + 1} verification runs across edits "
        f"produced the exact same failure output from `{name}`. Re-read the error traceback "
        "and check your assumptions before editing again.]"
    )


def repeated_failure_note(
    messages: Sequence[ChatMessage], name: str, arguments: str, output: str
) -> str | None:
    """A warning for ``output`` when the identical call already failed twice before, else ``None``."""
    if not looks_like_failure(output):
        return None

    events = _history_events(messages)
    wanted = _canonical(arguments)
    is_verify = _is_verify_call(name, arguments)

    # Check whether workspace mutations occurred since any earlier identical failure
    mutations_since_last_same = False
    prior_same_failures = 0
    for _, cname, cargs, res, is_mutation in events:
        if cname == name and cargs == wanted and looks_like_failure(res):
            prior_same_failures += 1
            mutations_since_last_same = False
        elif is_mutation:
            mutations_since_last_same = True

    if is_verify and mutations_since_last_same:
        # Files changed between runs: only warn if the failure signature itself is stagnant
        return _verification_stagnation_note(events, name, arguments, output)

    # If mutations occurred between runs for a verify tool, do not ban the tool
    if is_verify and prior_same_failures >= REPEAT_AFTER_FAILURES and mutations_since_last_same:
        return None

    # Count how many unmutated identical failures occurred
    unmutated_failures = 0
    for _, cname, cargs, res, is_mutation in reversed(events):
        if is_mutation:
            break
        if cname == name and cargs == wanted and looks_like_failure(res):
            unmutated_failures += 1

    if unmutated_failures < REPEAT_AFTER_FAILURES:
        return None
    return (
        f"[Repeated failure: this exact `{name}` call has now failed {unmutated_failures + 1} times. "
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
