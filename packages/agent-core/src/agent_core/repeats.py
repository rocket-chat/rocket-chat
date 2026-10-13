"""Noticing an agent that sends the same failing call again and again.

The error text of a rejected call is usually clear, yet some models resend it unchanged many
times. The call still runs (a retry can legitimately succeed), but once it has failed twice with
identical arguments the result carries an explicit instruction to change course.
"""

from __future__ import annotations

from collections.abc import Sequence

from specifications.interfaces.llm import ChatMessage

REPEAT_AFTER_FAILURES = 2
_ERROR_MARKERS = ("error", "failed", "blocked", "refused", "invalid", "not found")


def looks_like_failure(output: str) -> bool:
    head = output[:200].lower()
    return any(marker in head for marker in _ERROR_MARKERS)


def repeated_failure_note(
    messages: Sequence[ChatMessage], name: str, arguments: str, output: str
) -> str | None:
    """A warning for ``output`` when the identical call already failed twice before, else ``None``."""
    if not looks_like_failure(output):
        return None
    results = {m.tool_call_id: m.content or "" for m in messages if m.role == "tool"}
    failures = sum(
        1
        for message in messages
        for call in message.tool_calls or []
        if call.get("function", {}).get("name") == name
        and call.get("function", {}).get("arguments") == arguments
        and looks_like_failure(results.get(call.get("id"), ""))
    )
    if failures < REPEAT_AFTER_FAILURES:
        return None
    return (
        f"[Repeated failure: this exact `{name}` call has now failed {failures + 1} times. "
        "Do not send it again. Read the error, change the arguments, or use another approach; "
        "if you cannot, tell the user what is blocking you.]"
    )
