"""Model replies cut off at the output-token limit.

A tool call that was being written when the limit hit has unparseable arguments: running it
would fail with a confusing "arguments are not valid JSON", and the model would likely write the
same oversized call again. It is not dispatched; the model is told why and how to split the work.
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
