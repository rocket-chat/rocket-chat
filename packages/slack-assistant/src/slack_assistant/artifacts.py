"""Content that does not belong in a chat message goes up as a Slack file.

Slack truncates long messages and renders big diffs badly, so a long answer or a large change
is summarised in the thread and attached in full as a file (a diff reads well as ``.diff``).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from specifications.interfaces.agent import FileDiff

logger = logging.getLogger(__name__)

SCREENSHOT_PATH = re.compile(r"/workspace/\.rocket/screenshots/[\w.\-]+\.(?:png|jpe?g)")
MAX_INLINE_DIFF_LINES = 80


def combined_diff(diffs: list[FileDiff]) -> str:
    return "\n".join(f"--- {d.path}\n{d.diff_content.rstrip()}\n" for d in diffs)


def diff_is_large(diffs: list[FileDiff]) -> bool:
    return sum(d.diff_content.count("\n") + 1 for d in diffs) > MAX_INLINE_DIFF_LINES


def screenshot_paths(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in SCREENSHOT_PATH.findall(text or ""):
        seen[match] = None
    return list(seen)


def head_of(text: str, limit: int) -> str:
    """The start of ``text`` cut at a paragraph or line boundary where there is one."""
    if len(text) <= limit:
        return text
    window = text[:limit]
    for separator in ("\n\n", "\n", " "):
        cut = window.rfind(separator)
        if cut > limit // 2:
            return window[:cut].rstrip() + " …"
    return window.rstrip() + " …"


async def upload_file(
    client: Any,
    *,
    channel_id: str,
    thread_ts: str,
    filename: str,
    content: str | bytes,
    title: str,
    comment: str | None = None,
) -> bool:
    """Upload one file into the thread; False (and a log line) when Slack refuses."""
    try:
        await client.files_upload_v2(
            channel=channel_id,
            thread_ts=thread_ts,
            filename=filename,
            title=title,
            initial_comment=comment,
            **({"content": content} if isinstance(content, str) else {"file": content}),
        )
        return True
    except Exception as err:
        logger.warning("Slack file upload %s failed: %s", filename, err)
        return False
