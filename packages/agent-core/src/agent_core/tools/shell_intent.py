"""What a shell command is for, when a dedicated tool does the same job.

Searching, reading, listing and editing files through ``bash_exec`` works, but the dedicated tools
(``grep_search``, ``file_read``, ``glob_files``, ``file_edit``) return structured, clamped output
and are classified as reads or edits by the permission layer. A model that drifts to the shell
anyway is shown a short tip with the result, never refused: the command already ran and a refusal
would only cost a round trip. Pipelines whose first stage is not a file operation (``go test |
tail``, ``git log | head``) are the shell's real job and are never flagged.
"""

from __future__ import annotations

import re
from enum import StrEnum


class ShellIntent(StrEnum):
    SEARCH = "search"
    READ = "read"
    LIST = "list"
    EDIT = "edit"


TIPS: dict[ShellIntent, str] = {
    ShellIntent.SEARCH: (
        "`grep_search` searches files with structured, clamped output "
        "(`context_lines`, `file_glob`, `files_only`)."
    ),
    ShellIntent.READ: "`file_read` reads a numbered line range (`start_line`, `end_line`).",
    ShellIntent.LIST: "`glob_files` finds files by pattern and honours .gitignore.",
    ShellIntent.EDIT: "`file_edit`, `apply_patch` and `file_write` make reviewable edits.",
}
# Calls through the shell before the first tip, then one tip every ``TIP_EVERY`` such calls.
TIP_AFTER = 2
TIP_EVERY = 3

_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*(?:\n|$)", re.DOTALL)
_WRITE_REDIRECT = re.compile(r"(?<![0-9&>])>{1,2}\s*(?!/dev/null|&)\S")
_SEARCHERS = frozenset({"grep", "egrep", "fgrep", "rg", "ag"})
_READERS = frozenset({"cat", "head", "tail", "less", "more", "nl"})
_SEPARATORS = (";", "&&", "||", "\n")


def _segments(command: str) -> list[str]:
    """The commands of a line, split on ``;``, ``&&``, ``||`` and newlines outside quotes."""
    segments: list[str] = []
    current: list[str] = []
    quote = ""
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            if char == quote:
                quote = ""
            current.append(char)
            index += 1
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            index += 1
            continue
        separator = next((s for s in _SEPARATORS if command.startswith(s, index)), "")
        if separator:
            segments.append("".join(current))
            current = []
            index += len(separator)
            continue
        current.append(char)
        index += 1
    segments.append("".join(current))
    return [s.strip() for s in segments if s.strip()]


def _first_stage(segment: str) -> str:
    """The segment up to its first pipe: later stages only post-process its output."""
    quote = ""
    for index, char in enumerate(segment):
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "|" and segment[index : index + 2] != "||":
            return segment[:index].strip()
    return segment


def _operands(words: list[str]) -> list[str]:
    """Words that are neither options nor an option's value we can tell apart."""
    return [w for w in words[1:] if not w.startswith("-")]


def _intent_of(segment: str) -> ShellIntent | None:
    stage = _first_stage(segment)
    words = stage.split()
    if not words:
        return None
    command = words[0].rsplit("/", 1)[-1]
    operands = _operands(words)
    if _WRITE_REDIRECT.search(stage) and command in {"echo", "printf", "cat", "tee"}:
        return ShellIntent.EDIT
    if command == "tee" and operands:
        return ShellIntent.EDIT
    if command == "sed":
        if any(w.startswith("-i") or w == "--in-place" for w in words[1:]):
            return ShellIntent.EDIT
        # ``sed -n '10,40p' file`` is a read; ``sed s/a/b/ file | …`` only transforms.
        if "-n" in words[1:] and len(operands) >= 2:
            return ShellIntent.READ
        return None
    if command in _SEARCHERS:
        # ``grep -r pattern`` or ``grep pattern file``: a pattern and somewhere to look.
        return ShellIntent.SEARCH if len(operands) >= 2 or _recursive(words) else None
    if command in _READERS:
        return ShellIntent.READ if operands and not _is_numeric_option_only(operands) else None
    if command == "find":
        return ShellIntent.LIST if re.search(r"-i?name|-path|-type\s+f", stage) else None
    if command == "ls" and any(w.startswith("-") and "R" in w for w in words[1:]):
        return ShellIntent.LIST
    return None


def _recursive(words: list[str]) -> bool:
    """Whether the search walks directories without being given a file (rg always does)."""
    if words[0] in {"rg", "ag"}:
        return True
    return any(
        w in {"-r", "-R", "--recursive"}
        or (w.startswith("-") and not w.startswith("--") and "r" in w)
        for w in words[1:]
    )


def _is_numeric_option_only(operands: list[str]) -> bool:
    return all(o.isdigit() for o in operands)


def shell_intents(command: str) -> frozenset[ShellIntent]:
    """The file operations ``command`` performs that a dedicated tool also covers."""
    if re.search(r"\b(cat|tee)\b[^\n|]*>>?\s*\S+[^\n]*<<", command):
        return frozenset({ShellIntent.EDIT})
    stripped = _HEREDOC.sub("\n", command)
    found = {_intent_of(segment) for segment in _segments(stripped)}
    return frozenset(i for i in found if i is not None)


def tip_for(intents: frozenset[ShellIntent]) -> str | None:
    """The single tip for the most valuable replacement among ``intents``."""
    for intent in (ShellIntent.SEARCH, ShellIntent.READ, ShellIntent.LIST, ShellIntent.EDIT):
        if intent in intents:
            return TIPS[intent]
    return None


def should_tip(misuses_so_far: int) -> bool:
    """Whether the ``misuses_so_far``-th (1-based) shell file operation of a turn gets a tip."""
    return misuses_so_far >= TIP_AFTER and (misuses_so_far - TIP_AFTER) % TIP_EVERY == 0
