"""What a compaction asks the model for, how its answer is checked, and what replaces a bad one.

A compaction replaces the middle of a long conversation with a summary, so the summary is all the
agent keeps of that work. Two real compactions stored nothing usable: one an empty answer (a
reasoning model spent its output budget thinking), the other a tool call in the model's own
markup (it continued the agentic conversation it was shown instead of summarising it). After both,
the agent re-read the files it had already changed and searched for code it had deleted.

So the summary is asked for on the conversation itself, with the template and rules below (after
opencode's), checked (``valid_summary``) and asked for once more with a stricter note; when that
fails too, ``tool_history_digest`` rebuilds a factual record from the tool calls: files changed,
commands and their outcome, commits, pushes, pull requests and waits. It cannot be empty and it
cannot invent anything.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from agent_core.leaked_calls import contains_leaked_calls
from specifications.interfaces.llm import ChatMessage

SUMMARY_STATUS_MODEL = "model"
SUMMARY_STATUS_MODEL_RETRY = "model_retry"
SUMMARY_STATUS_DIGEST = "digest"

# Headings without which a summary cannot resume the work: the goal, and the next move.
REQUIRED_HEADINGS = ("## Objective", "## Next Move")
MIN_SUMMARY_CHARS = 120
# Marks a bridge that already holds a summary, so the next compaction updates it.
CHECKPOINT_MARKER = "Summary of the compacted work:"

_TEMPLATE = """Use exactly this format (leave out a section only when it does not apply):

## Objective
- [one or two sentences: what the person is trying to accomplish]

## Requirements
- [constraints, preferences and scope the person stated, in their words where it matters, or "(none)"]

## Decisions
- [decisions already made and why, or "(none)"]

## Work State
### Completed
- [goals completed, or "(none)"]
### Active
- [goals in progress, or "(none)"]
### Blocked
- [what blocks progress and why, or "(none)"]

## Next Move
1. [the next actions, in order]

## Relevant Files
- `[path]`: [why it matters] (at most 15, most important first)

## Important Context
- [facts the next step cannot continue without and cannot easily find again, or "(none)"]"""

_RULES = """Rules:
- Terse single-line bullets, no prose paragraphs.
- Keep exact file paths, symbols, commands, error messages, URLs, branch names and identifiers.
- Say whether changes are uncommitted, committed, pushed, in a pull request, or merged.
- For multi-service or monorepo work, explicitly list which services/packages passed tests and which remain.
- Carry forward only requests of the person that are still open, in their exact words.
- Leave out the instructions you were given rather than told by the person (repository
  instructions such as AGENTS.md, skills, environment setup): they are given again separately.
- Do not mention the summary or the compaction."""

SUMMARY_PROMPT = "\n\n".join(
    [
        "Summarize the conversation above into a checkpoint another agent will resume the work "
        "from. Do not continue the task and do not call tools: answer with the summary only, "
        "as text.",
        _TEMPLATE,
        _RULES,
    ]
)

UPDATE_PROMPT = "\n\n".join(
    [
        "The conversation above contains an earlier checkpoint (after 'Summary of the compacted "
        "work'). Update it into one checkpoint covering everything since: newer history takes "
        "precedence; move finished work out of Active, drop resolved blockers, keep open "
        "requests. Do not continue the task and do not call tools: answer with the summary "
        "only, as text.",
        _TEMPLATE,
        _RULES,
    ]
)

RETRY_NOTE = (
    "Your previous answer was not the summary. Do not call tools and do not continue the work. "
    "Answer now with the summary as plain text, using the exact headings of the format."
)

DIGEST_HEADER = (
    "(Rebuilt from the tool history: the model's summary was unusable. It lists what was done, "
    "not why.)"
)
MAX_DIGEST_ITEMS = 25
_EXIT_CODE = re.compile(r"exit code (\d+)", re.IGNORECASE)
_URL = re.compile(r"https://github\.com/[^\s)\]]+/pull/\d+")
_HEAD = re.compile(r"Head: ([0-9a-f]{7,40})")
_EDIT_TOOLS = frozenset({"file_edit", "file_write", "apply_patch", "apply_diff_file"})


def summary_prompt(messages: Sequence[ChatMessage]) -> str:
    """The prompt for ``messages``: an update when they already carry a checkpoint."""
    has_checkpoint = any(
        m.role == "user" and CHECKPOINT_MARKER in (m.content or "") for m in messages
    )
    return UPDATE_PROMPT if has_checkpoint else SUMMARY_PROMPT


def valid_summary(text: str | None) -> bool:
    """Whether ``text`` can stand in for the work it summarises."""
    body = (text or "").strip()
    return (
        len(body) >= MIN_SUMMARY_CHARS
        and not contains_leaked_calls(body)
        and all(heading in body for heading in REQUIRED_HEADINGS)
    )


def _arguments(call: dict[str, Any]) -> dict[str, Any]:
    raw = (call.get("function") or {}).get("arguments")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _results_by_call(messages: Sequence[ChatMessage]) -> dict[str, str]:
    return {
        str(m.tool_call_id): m.content or ""
        for m in messages
        if m.role == "tool" and m.tool_call_id
    }


def _outcome(result: str) -> str:
    code = _EXIT_CODE.search(result)
    return f"exit {code.group(1)}" if code else "ok"


def _record(name: str, args: dict[str, Any], result: str) -> tuple[str, str] | None:
    """(kind, line) for one call worth remembering, or ``None``."""
    first_line = result.strip().splitlines()[0][:160] if result.strip() else ""
    if name in _EDIT_TOOLS:
        path = args.get("path") or args.get("file_path") or "(a patch)"
        return "files", f"`{path}`"
    if name == "bash_exec":
        command = " ".join(str(args.get("command") or "").split())[:160]
        return "commands", f"`{command}` ({_outcome(result)})"
    if name == "git_commit":
        return "git", f"commit: {str(args.get('message') or '').splitlines()[0][:120]}"
    if name == "git_push":
        head = _HEAD.search(result)
        return "git", f"pushed{f' head {head.group(1)}' if head else ''}"
    if name in ("git_open_pr", "github_pull_request"):
        urls = sorted(set(_URL.findall(result)))
        return ("git", f"{name}: {', '.join(urls)}") if urls else None
    if name == "wait_for":
        return "waits", first_line or "wait registered"
    return None


def tool_history_digest(middle: Sequence[ChatMessage]) -> str:
    """A factual record of the work in ``middle``, from its tool calls and their results."""
    results = _results_by_call(middle)
    sections: dict[str, list[str]] = {"files": [], "commands": [], "git": [], "waits": []}
    for message in middle:
        for call in message.tool_calls or []:
            name = str((call.get("function") or {}).get("name") or "")
            entry = _record(name, _arguments(call), results.get(str(call.get("id")), ""))
            if entry is not None and entry[1] not in sections[entry[0]]:
                sections[entry[0]].append(entry[1])
    titles = {
        "files": "Files changed",
        "commands": "Commands run (latest last)",
        "git": "Commits, pushes and pull requests",
        "waits": "Waits",
    }
    parts = [DIGEST_HEADER]
    for key, lines in sections.items():
        if lines:
            shown = lines[-MAX_DIGEST_ITEMS:]
            parts.append(f"## {titles[key]}\n" + "\n".join(f"- {line}" for line in shown))
    if len(parts) == 1:
        parts.append("No file change, command or publication was made in the compacted steps.")
    return "\n\n".join(parts)
