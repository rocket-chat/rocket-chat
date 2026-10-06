"""Unified diff parsing and application engine."""

import re
from dataclasses import dataclass


@dataclass
class _DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]


def _parse_hunk_header(header: str) -> _DiffHunk | None:
    match = re.match(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@", header)
    if not match:
        return None
    old_start = int(match.group(1))
    old_count = int(match.group(2)) if match.group(2) is not None else 1
    new_start = int(match.group(3))
    new_count = int(match.group(4)) if match.group(4) is not None else 1
    return _DiffHunk(
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        lines=[],
    )


def _split_into_hunks(patch_text: str) -> list[_DiffHunk]:
    hunks: list[_DiffHunk] = []
    current_hunk: _DiffHunk | None = None

    for raw_line in patch_text.splitlines():
        if raw_line.startswith("@@"):
            current_hunk = _parse_hunk_header(raw_line)
            if current_hunk:
                hunks.append(current_hunk)
            continue

        if current_hunk is not None:
            if raw_line.startswith(("+", "-", " ", "\\")):
                current_hunk.lines.append(raw_line)

    return hunks


def _apply_single_hunk(
    source_lines: list[str],
    hunk: _DiffHunk,
    offset: int,
) -> tuple[bool, list[str], int]:
    expected_old_lines: list[str] = []
    replacement_lines: list[str] = []

    for line in hunk.lines:
        prefix = line[0] if line else ""
        content = line[1:] if len(line) > 1 else ""
        if prefix in (" ", "-"):
            expected_old_lines.append(content)
        if prefix in (" ", "+"):
            replacement_lines.append(content)

    target_start = max(0, hunk.old_start - 1 + offset)
    target_end = target_start + len(expected_old_lines)

    if (
        target_end <= len(source_lines)
        and source_lines[target_start:target_end] == expected_old_lines
    ):
        new_lines = source_lines[:target_start] + replacement_lines + source_lines[target_end:]
        new_offset = offset + (len(replacement_lines) - len(expected_old_lines))
        return True, new_lines, new_offset

    # Fallback fuzzy matching within search window when upstream line offsets shifted
    window = 10
    search_min = max(0, target_start - window)
    search_max = min(len(source_lines) - len(expected_old_lines), target_start + window)

    for candidate_start in range(search_min, search_max + 1):
        candidate_end = candidate_start + len(expected_old_lines)
        if source_lines[candidate_start:candidate_end] == expected_old_lines:
            new_lines = (
                source_lines[:candidate_start] + replacement_lines + source_lines[candidate_end:]
            )
            new_offset = (
                offset
                + (candidate_start - target_start)
                + (len(replacement_lines) - len(expected_old_lines))
            )
            return True, new_lines, new_offset

    return False, source_lines, offset


def apply_unified_diff(original: str, patch_text: str) -> tuple[bool, str]:
    """
    Apply a unified diff patch to source text.

    Returns a tuple of (success, patched_text). If any hunk fails to match,
    returns (False, original) to prevent partial corruption.
    """
    hunks = _split_into_hunks(patch_text)
    if not hunks:
        return False, original

    source_lines = original.splitlines()
    has_trailing_newline = original.endswith("\n") if original else True
    current_lines = list(source_lines)
    offset = 0

    for hunk in hunks:
        success, current_lines, offset = _apply_single_hunk(current_lines, hunk, offset)
        if not success:
            return False, original

    result = "\n".join(current_lines)
    if has_trailing_newline and result:
        result += "\n"
    return True, result
