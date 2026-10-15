"""Deterministic head/tail clamping that keeps tool output inside the model's budget.

Long output keeps its head (command start-up, file header) and its tail (stack traces, test
summaries) and says exactly what was hidden, so the agent can ask for it. The byte cap holds
whatever the shape of the output: a few minified lines are trimmed as surely as many short ones.
"""

DEFAULT_MAX_LINES = 400
DEFAULT_MAX_BYTES = 25_000
HEAD_LINES = 80
TAIL_LINES = 200
# A single line longer than this is cut: minified code or a data blob is unreadable anyway.
MAX_LINE_CHARS = 2_000
DEFAULT_HINT = "narrow the command (grep, head, tail, sed -n) to see them"


def _trim_long_lines(lines: list[str]) -> list[str]:
    trimmed: list[str] = []
    for line in lines:
        body = line.rstrip("\n")
        if len(body) > MAX_LINE_CHARS:
            ending = "\n" if line.endswith("\n") else ""
            body = f"{body[:MAX_LINE_CHARS]}… [line cut: {len(body):,} characters]"
            line = body + ending
        trimmed.append(line)
    return trimmed


def _byte_cap(text: str, max_bytes: int) -> str:
    """Head and tail of ``text`` within ``max_bytes`` (UTF-8), with what was dropped in between."""
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    head = data[: max_bytes * 3 // 10].decode("utf-8", errors="ignore")
    tail = data[-(max_bytes * 6 // 10) :].decode("utf-8", errors="ignore")
    hidden = len(data) - len(head.encode()) - len(tail.encode())
    return f"{head}\n\n... [{hidden:,} bytes hidden to fit the output budget] ...\n\n{tail}"


def clamp_output(
    text: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    head_lines: int = HEAD_LINES,
    tail_lines: int = TAIL_LINES,
    *,
    hint: str = DEFAULT_HINT,
) -> str:
    """``text`` within ``max_lines`` lines and ``max_bytes`` bytes, naming what it hid."""
    if not text:
        return text
    lines = text.splitlines(keepends=True)
    if len(lines) <= max_lines and len(text.encode("utf-8")) <= max_bytes:
        return text
    lines = _trim_long_lines(lines)
    if len(lines) > max_lines and len(lines) > head_lines + tail_lines:
        first_hidden, last_hidden = head_lines + 1, len(lines) - tail_lines
        marker = f"\n... [lines {first_hidden:,}-{last_hidden:,} of {len(lines):,} hidden; {hint}] ...\n\n"
        clamped = "".join(lines[:head_lines]) + marker + "".join(lines[-tail_lines:])
    else:
        clamped = "".join(lines)
    return _byte_cap(clamped, max_bytes)
