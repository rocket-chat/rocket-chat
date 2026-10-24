"""Model replies cut off at the output-token limit, or that came back empty.

A tool call that was being written when the limit hit has unparseable arguments: running it
would fail with a confusing "arguments are not valid JSON", and the model would likely write the
same oversized call again. It is not dispatched; the model is told why and how to split the work.

A reasoning model can also spend the whole output budget thinking and stop before writing
anything. Such a reply has neither text nor a tool call; taking it as the final answer ended long
turns mid-task with an empty answer. The model is told and continues instead.
"""

from __future__ import annotations

import json
from typing import Any

TRUNCATED = "length"


def _complete(call: dict[str, Any]) -> bool:
    arguments = call.get("arguments") or "{}"
    try:
        return isinstance(json.loads(arguments), dict)
    except ValueError:
        return False


def split_truncated(
    calls: dict[int, dict[str, Any]], finish_reason: str | None
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """The calls that can run, and those cut off by the token limit (empty unless it hit)."""
    if finish_reason != TRUNCATED:
        return calls, []
    complete = {index: call for index, call in calls.items() if _complete(call)}
    cut = [call for index, call in calls.items() if index not in complete]
    return complete, cut


def truncation_notice(cut: list[dict[str, Any]]) -> str:
    names = ", ".join(f"`{call.get('name') or 'a tool'}`" for call in cut)
    size = sum(len(call.get("arguments") or "") for call in cut)
    return (
        f"Your last reply was cut off at the output token limit while writing a call to {names} "
        f"({size:,} characters of arguments so far), so that call did not run. Split the work "
        "into smaller calls: write a large file in parts (file_write the first part, then "
        "file_edit to add the rest) or make several smaller edits."
    )


# Empty replies in a row before the turn ends with an explanation instead of an answer.
MAX_EMPTY_RETRIES = 2


def empty_reply_notice(finish_reason: str | None, remaining_steps: int | None = None) -> str:
    if finish_reason == TRUNCATED:
        steps_note = (
            f" You have {remaining_steps} steps remaining in this turn."
            if remaining_steps is not None
            else ""
        )
        return (
            "Your last reply reached the output token limit while you were still reasoning, so "
            "it contained no answer and no tool call, and nothing happened. Reason less before "
            "acting: take the next concrete step now with a tool call (or give your answer), "
            f"and work through long analyses one step at a time.{steps_note}"
        )
    return (
        "Your last reply was empty: no answer and no tool call, so nothing happened. Continue "
        "the task: call the next tool, or give your final answer."
    )


def empty_reply_give_up(finish_reason: str | None) -> str:
    cause = (
        "kept reaching its output token limit while reasoning"
        if finish_reason == TRUNCATED
        else "returned empty replies"
    )
    return (
        f"I stopped here: the model {cause}, so this turn has no answer. Send a message to "
        "continue from where it stopped. If this repeats, an administrator can raise the "
        "maximum completion tokens (Settings > Models & routing) or choose another model."
    )


def is_dangling_tool_preface(text: str | None) -> bool:
    """Whether a tool-less reply is only a short announcement of a tool call that never came.

    Models sometimes stop at the colon before a tool call (e.g. "Reading the raw rows to see
    the exact error:") when an upstream stream closes at the tool boundary. Treating that
    preface as the final answer ends the turn before the action it announced.
    """
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 400 or not stripped.endswith(":"):
        return False
    return "\n\n" not in stripped and not stripped.startswith("#")


def dangling_preface_notice() -> str:
    return (
        "Your last reply ended with ':' as if about to call a tool, but included no tool call, "
        "so nothing ran. Emit the tool call now, or give your complete final answer."
    )
