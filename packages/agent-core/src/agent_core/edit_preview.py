"""A unified diff of what an edit tool call is about to do (or did), from its arguments alone.

Shown on an approval card before the edit runs and on a shared conversation afterwards, so it
never reads the sandbox: a ``file_edit`` shows its block replacement, a patch shows itself and a
``file_write`` shows the new content as added lines. Every preview is bounded and says so when cut.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from typing import Any

CUT_NOTICE = "\n… (cut at {limit} characters)"


def clamp_text(text: str, limit: int) -> str:
    """``text`` within ``limit`` characters, saying where it was cut."""
    if len(text) <= limit:
        return text
    notice = CUT_NOTICE.format(limit=limit)
    return text[: max(0, limit - len(notice))] + notice


def _replacement_diff(path: str, old: str, new: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(line if line.endswith("\n") else f"{line}\n" for line in lines)


def edit_preview(name: str, arguments: Mapping[str, Any], limit: int) -> str:
    """The diff ``name`` called with ``arguments`` applies, or ``""`` for a call that edits nothing."""
    path = str(arguments.get("path") or "file").lstrip("/")
    if name == "file_edit":
        text = _replacement_diff(
            path,
            str(arguments.get("target_block") or ""),
            str(arguments.get("replacement_block") or ""),
        )
    elif name == "file_write":
        text = _replacement_diff(path, "", str(arguments.get("content") or ""))
    elif name == "apply_patch":
        text = str(arguments.get("patch_content") or "")
    elif name == "apply_diff_file":
        text = str(arguments.get("diff_content") or "")
    else:
        return ""
    return clamp_text(text, limit)
