"""Resilient AST and block-replacement code editor producing unified FileDiffs."""

import difflib

from specifications.interfaces.agent import FileDiff


def create_file_diff(
    path: str,
    original_content: str,
    modified_content: str,
    is_new_file: bool = False,
) -> FileDiff:
    """Generate a unified diff between original and modified file contents."""
    orig_lines = original_content.splitlines(keepends=True)
    mod_lines = modified_content.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            orig_lines,
            mod_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )

    diff_text = "".join(diff_lines)
    additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

    return FileDiff(
        path=path,
        diff_content=diff_text,
        additions=additions,
        deletions=deletions,
        is_new_file=is_new_file,
    )


def _find_single_match_index(source_lines: list[str], target_lines: list[str]) -> int:
    """Find the single matching slice of target_lines inside source_lines or raise ValueError."""
    target_len = len(target_lines)
    if target_len == 0:
        raise ValueError("Target block cannot be empty.")

    stripped_targets = [line.rstrip() for line in target_lines]
    match_indices: list[int] = []

    for i in range(len(source_lines) - target_len + 1):
        window = [line.rstrip() for line in source_lines[i : i + target_len]]
        if window == stripped_targets:
            match_indices.append(i)

    if not match_indices:
        raise ValueError("Target block not found in source text. Ensure context matches.")

    if len(match_indices) > 1:
        raise ValueError(
            f"Target block matches {len(match_indices)} locations in source text. "
            "Provide more surrounding context to disambiguate."
        )

    return match_indices[0]


def replace_block(source_text: str, target_block: str, replacement_block: str) -> str:
    """
    Replace target_block with replacement_block inside source_text.

    Supports exact matching and whitespace-trimmed line matching.
    Errors if 0 or more than 1 matches are detected.
    """
    # 1. Exact string substitution if unambiguous
    occurrences = source_text.count(target_block)
    if occurrences == 1:
        return source_text.replace(target_block, replacement_block, 1)

    if occurrences > 1:
        raise ValueError(
            f"Target block matches {occurrences} exact locations in source text. "
            "Provide more surrounding context to disambiguate."
        )

    # 2. Resilient line-by-line whitespace-trimmed matching
    source_lines = source_text.splitlines()
    target_lines = target_block.splitlines()
    replacement_lines = replacement_block.splitlines()

    match_idx = _find_single_match_index(source_lines, target_lines)
    new_lines = (
        source_lines[:match_idx] + replacement_lines + source_lines[match_idx + len(target_lines) :]
    )

    has_trailing_newline = source_text.endswith("\n") if source_text else True
    result = "\n".join(new_lines)
    if has_trailing_newline and not result.endswith("\n"):
        result += "\n"

    return result
