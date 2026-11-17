"""Offload large tool execution outputs to workspace files.

Tool results exceeding the threshold retain head (40 lines) and tail (20 lines)
in active context while offloading the full content to `/workspace/.rocket/outputs/`.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

from agent_core.tools.compact_json import compact_structured_payload
from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.session import SessionFilesProtocol

logger = logging.getLogger(__name__)

DEFAULT_OFFLOAD_THRESHOLD = 6_000
OFFLOAD_HEAD_LINES = 40
OFFLOAD_TAIL_LINES = 20
# One very long line (minified JS, a JSON blob) must not defeat the line budget.
MAX_EXCERPT_LINE_CHARS = 400
# An excerpt may exceed the threshold a little (its lines are whole); past this it is cut by characters.
EXCERPT_SLACK = 2

# Offloaded outputs also stay readable without a sandbox (MCP and other Tier-1 tools), but only
# for as long as the process needs them: bounded by bytes, least recently used first.
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_SESSION_CACHE_BYTES = 4 * 1024 * 1024
_OFFLOAD_CACHE: OrderedDict[tuple[str, str], str] = OrderedDict()
_cache_bytes = 0

OUTPUT_DIRECTORY = "/workspace/.rocket/outputs"
OUTPUT_RETENTION_DAYS = 2
_cleaned_sessions: set[str] = set()


def _normalise(path: str) -> str:
    return path.strip().lstrip("/").removeprefix("workspace/").removeprefix("./")


def get_cached_offload(session_id: str, path: str) -> str | None:
    """Retrieve offloaded tool output from in-memory cache if present."""
    key = (session_id, _normalise(path))
    content = _OFFLOAD_CACHE.get(key)
    if content is not None:
        _OFFLOAD_CACHE.move_to_end(key)
    return content


def _evict(key: tuple[str, str]) -> None:
    global _cache_bytes
    content = _OFFLOAD_CACHE.pop(key, None)
    if content is not None:
        _cache_bytes -= len(content)


def _oldest(session_id: str | None, keep: tuple[str, str]) -> tuple[str, str] | None:
    """The least recently used entry (of one session, or of all) other than ``keep``."""
    return next(
        (k for k in _OFFLOAD_CACHE if k != keep and (session_id is None or k[0] == session_id)),
        None,
    )


def store_cached_offload(session_id: str, path: str, content: str) -> None:
    """Store offloaded tool output in memory for sandbox-free access."""
    global _cache_bytes
    key = (session_id, _normalise(path))
    _evict(key)
    if len(content) > MAX_SESSION_CACHE_BYTES:
        return
    _OFFLOAD_CACHE[key] = content
    _cache_bytes += len(content)
    while (
        sum(len(c) for (sid, _), c in _OFFLOAD_CACHE.items() if sid == session_id)
        > MAX_SESSION_CACHE_BYTES
        and (old := _oldest(session_id, key)) is not None
    ):
        _evict(old)
    while _cache_bytes > MAX_CACHE_BYTES and (old := _oldest(None, key)) is not None:
        _evict(old)


def forget_session_offloads(session_id: str) -> None:
    """Drop a session's cached outputs (it was deleted or reset)."""
    for key in [k for k in _OFFLOAD_CACHE if k[0] == session_id]:
        _evict(key)
    _cleaned_sessions.discard(session_id)


async def _remove_old_outputs(driver: Any, session_id: str) -> None:
    """Delete offloaded outputs older than the retention window, once per session per process.

    The workspace volume outlives the sandbox, so without this every long session leaves its
    command output on disk for good.
    """
    if session_id in _cleaned_sessions or not hasattr(driver, "exec_command"):
        return
    _cleaned_sessions.add(session_id)
    try:
        await driver.exec_command(
            session_id,
            f"find {OUTPUT_DIRECTORY} -type f -mtime +{OUTPUT_RETENTION_DAYS} -delete 2>/dev/null; true",
            timeout_seconds=10,
        )
    except Exception as err:
        logger.debug("Could not clean old offloaded outputs for %s: %s", session_id, err)


async def _keep_output(
    session_id: str, relative_path: str, output: str, session_files: SessionFilesProtocol | None
) -> None:
    if session_files is not None:
        try:
            await session_files.put(relative_path, output, kind="output")
            return
        except Exception as err:
            logger.warning("Session file store unavailable for %s: %s", relative_path, err)
    store_cached_offload(session_id, relative_path, output)


async def _write_into_running_sandbox(
    driver: Any, session_id: str, relative_path: str, output: str
) -> None:
    """Copy the output into a running sandbox; never start or wake one for it.

    A stopped sandbox gets the file when it starts (the platform copies the session's files
    into a new workspace), so a sandbox-free turn never pays for a sandbox to keep an output.
    """
    if driver is None or not hasattr(driver, "write_file"):
        return
    try:
        if hasattr(driver, "get_status") and (
            await driver.get_status(session_id) != SandboxStatus.RUNNING
        ):
            return
        await driver.write_file(session_id, relative_path, output, overwrite=True)
        await _remove_old_outputs(driver, session_id)
    except Exception as err:
        logger.debug("Sandbox copy of offload file %s skipped: %s", relative_path, err)


async def read_offloaded(
    session_id: str, path: str, session_files: SessionFilesProtocol | None
) -> str | None:
    """An offloaded output or sandbox-free file by workspace path, from wherever it is kept."""
    if session_files is not None:
        try:
            kept = await session_files.get(path)
        except Exception as err:
            logger.debug("Session file read failed for %s: %s", path, err)
            kept = None
        if kept is not None:
            return kept.content
    return get_cached_offload(session_id, path)


def _short(line: str) -> str:
    if len(line) <= MAX_EXCERPT_LINE_CHARS:
        return line
    newline = "\n" if line.endswith("\n") else ""
    return (
        line[:MAX_EXCERPT_LINE_CHARS].rstrip("\n")
        + f" … [{len(line) - MAX_EXCERPT_LINE_CHARS:,} chars cut]"
        + newline
    )


def _by_characters(text: str, threshold: int, save_notice: str) -> str:
    half = threshold // 2
    head_text, tail_text = text[:half], text[-half:]
    marker = (
        f"\n\n... [{len(text) - len(head_text) - len(tail_text):,} characters omitted. "
        f"{save_notice}] ...\n\n"
    )
    return head_text + marker + tail_text


async def offload_tool_output(
    output: str,
    session_id: str,
    call_id: str,
    threshold: int = DEFAULT_OFFLOAD_THRESHOLD,
    driver: Any = None,
    turn_index: int = 1,
    session_files: SessionFilesProtocol | None = None,
) -> str:
    """Offload output exceeding ``threshold`` chars to a session file, returning head + tail.

    The full output goes to the session's durable files when the turn has them (PostgreSQL:
    any replica, with or without a sandbox), else to the bounded in-process cache; and into
    the sandbox too when one is running, so shell tools find it.
    """
    if not output or len(output) <= threshold:
        return output

    filename = f"{turn_index}_{call_id}.txt"
    relative_path = f".rocket/outputs/{filename}"
    display_path = f"{OUTPUT_DIRECTORY}/{filename}"

    await _keep_output(session_id, relative_path, output, session_files)
    await _write_into_running_sandbox(driver, session_id, relative_path, output)

    compacted = compact_structured_payload(output)
    if len(compacted) <= threshold:
        return compacted

    save_notice = f"Full output saved to {display_path}. Use file_read to inspect middle sections."

    lines = compacted.splitlines(keepends=True)
    if len(lines) <= OFFLOAD_HEAD_LINES + OFFLOAD_TAIL_LINES:
        return _by_characters(compacted, threshold, save_notice)

    head = [_short(line) for line in lines[:OFFLOAD_HEAD_LINES]]
    tail = [_short(line) for line in lines[-OFFLOAD_TAIL_LINES:]]
    omitted = len(lines) - len(head) - len(tail)
    marker = f"\n... [{omitted:,} lines of {len(lines):,} omitted. {save_notice}] ...\n\n"
    excerpt = "".join(head) + marker + "".join(tail)
    if len(excerpt) > threshold * EXCERPT_SLACK:
        return _by_characters(compacted, threshold, save_notice)
    return excerpt
