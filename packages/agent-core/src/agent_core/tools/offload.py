"""Offload large tool execution outputs to workspace files.

Tool results exceeding the threshold retain head (40 lines) and tail (20 lines)
in active context while offloading the full content to `/workspace/.rocket/outputs/`.
"""

from __future__ import annotations

import logging
from typing import Any

from agent_core.tools.compact_json import compact_structured_payload

logger = logging.getLogger(__name__)

DEFAULT_OFFLOAD_THRESHOLD = 6_000
OFFLOAD_HEAD_LINES = 40
OFFLOAD_TAIL_LINES = 20


_OFFLOAD_CACHE: dict[tuple[str, str], str] = {}
_MAX_OFFLOAD_CACHE_ENTRIES = 256


def get_cached_offload(session_id: str, path: str) -> str | None:
    """Retrieve offloaded tool output from in-memory cache if present."""
    norm = path.strip().lstrip("/").removeprefix("workspace/").removeprefix("./")
    return _OFFLOAD_CACHE.get((session_id, norm))


def store_cached_offload(session_id: str, path: str, content: str) -> None:
    """Store offloaded tool output in memory for sandbox-free access."""
    norm = path.strip().lstrip("/").removeprefix("workspace/").removeprefix("./")
    if len(_OFFLOAD_CACHE) >= _MAX_OFFLOAD_CACHE_ENTRIES:
        first_key = next(iter(_OFFLOAD_CACHE))
        _OFFLOAD_CACHE.pop(first_key, None)
    _OFFLOAD_CACHE[(session_id, norm)] = content


async def offload_tool_output(
    output: str,
    session_id: str,
    call_id: str,
    threshold: int = DEFAULT_OFFLOAD_THRESHOLD,
    driver: Any = None,
    turn_index: int = 1,
) -> str:
    """Offload output exceeding ``threshold`` chars to workspace file, returning head + tail."""
    if not output or len(output) <= threshold:
        return output

    filename = f"{turn_index}_{call_id}.txt"
    relative_path = f".rocket/outputs/{filename}"
    display_path = f"/workspace/.rocket/outputs/{filename}"

    # Always persist in in-memory cache for Tier-1/MCP tools without a sandbox
    store_cached_offload(session_id, relative_path, output)

    if driver is not None and hasattr(driver, "write_file"):
        try:
            await driver.write_file(session_id, relative_path, output, overwrite=True)
        except Exception as err:
            logger.debug(
                "Sandbox write failed for offload file %s; using in-memory cache: %s",
                relative_path,
                err,
            )

    compacted = compact_structured_payload(output)
    if len(compacted) <= threshold:
        return compacted

    save_notice = f"Full output saved to {display_path}. Use file_read to inspect middle sections."

    lines = compacted.splitlines(keepends=True)
    if len(lines) <= OFFLOAD_HEAD_LINES + OFFLOAD_TAIL_LINES:
        half = threshold // 2
        head_text = compacted[:half]
        tail_text = compacted[-half:]
        marker = (
            f"\n\n... [{len(compacted) - len(head_text) - len(tail_text):,} characters omitted. "
            f"{save_notice}] ...\n\n"
        )
        return head_text + marker + tail_text

    head = lines[:OFFLOAD_HEAD_LINES]
    tail = lines[-OFFLOAD_TAIL_LINES:]
    omitted = len(lines) - len(head) - len(tail)
    marker = f"\n... [{omitted:,} lines of {len(lines):,} omitted. {save_notice}] ...\n\n"
    return "".join(head) + marker + "".join(tail)
