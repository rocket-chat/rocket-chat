"""Zero-loss deterministic head/tail output clamping to protect model context."""

DEFAULT_MAX_LINES = 400
DEFAULT_MAX_BYTES = 25_000
HEAD_LINES = 80
TAIL_LINES = 200


def clamp_output(
    text: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    head_lines: int = HEAD_LINES,
    tail_lines: int = TAIL_LINES,
) -> str:
    """
    Deterministic head/tail clamping for stdout, stderr, and file content.

    If output exceeds max_lines or max_bytes, preserves the leading lines
    (command startup/context) and trailing lines (stack traces/assertion errors)
    with a clear explanatory marker.
    """
    if not text:
        return text

    byte_len = len(text.encode("utf-8", errors="replace"))
    lines = text.splitlines(keepends=True)
    line_count = len(lines)

    if line_count <= max_lines and byte_len <= max_bytes:
        return text

    if line_count > (head_lines + tail_lines):
        kept_head = "".join(lines[:head_lines])
        kept_tail = "".join(lines[-tail_lines:])
        truncated_count = line_count - (head_lines + tail_lines)
        truncated_kb = (
            max(0, (byte_len - len(kept_head.encode()) - len(kept_tail.encode()))) // 1024
        )

        marker = (
            f"\n\n... [TRUNCATED {truncated_count} lines (~{truncated_kb}KB) of repetitive output. "
            "Inspect targeted lines or tail as needed] ...\n\n"
        )
        clamped = kept_head + marker + kept_tail
    else:
        # Exceeds max_bytes but not head_lines + tail_lines
        clamped = text[:max_bytes] + "\n\n... [TRUNCATED bytes limit exceeded] ...\n\n"

    return clamped
