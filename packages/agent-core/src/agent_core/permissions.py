"""Permission modes: how much of a turn's work waits for a human.

The model can always ask for approval itself (``request_approval``), but supervision that
depends on the model remembering to ask is not supervision. A mode makes the platform ask:
every tool call is classified and, depending on the session's mode, a human gate is raised
*before* the tool runs. The decision then flows through the same approval events and the
organization's approval policy as a model-initiated request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Calls that cannot change the workspace, the repository or the outside world.
READ_TOOLS: frozenset[str] = frozenset(
    {
        "file_read",
        "list_dir",
        "grep_search",
        "glob_files",
        "code_search",
        "git_status",
        "git_diff",
        "git_log",
        "kb_search",
        "search_tools",
        # Fetching code to read it: it adds files to the sandbox but changes no repository.
        "clone_repository",
        # Looking at, waiting for or stopping a process the session itself started.
        "process_list",
        "process_output",
        "process_wait",
        "process_stop",
        # Read-only GitHub lookups: nothing in the sandbox, repository or account changes.
        "github_find_repositories",
        "github_read_file",
        "github_issues",
        # Recording that the session follows a pull request; nothing on GitHub changes.
        "github_watch",
        "github_unwatch",
        "recall",
        "web_fetch",
        "web_search",
        "browser_inspect",
        "view_image",
        "think",
        "update_task_checklist",
        "update_status",
        "ask_question",
        "session_rename",
        "enter_plan_mode",
        "exit_plan_mode",
        "request_approval",
        "remember",
        "git_checkpoint",
        "delegate_subagent",
    }
)
EDIT_TOOLS: frozenset[str] = frozenset(
    {"file_write", "file_edit", "apply_patch", "apply_diff_file"}
)
# Publishing changes leaves the sandbox: commits, branches, pull requests.
PUBLISH_TOOLS: frozenset[str] = frozenset(
    {"git_commit", "git_branch", "git_push", "git_open_pr", "git_revert"}
)

MODES_GATING_EDITS: frozenset[str] = frozenset({"ask"})
MODES_GATING_COMMANDS: frozenset[str] = frozenset({"ask", "auto_edit"})

MAX_DESCRIPTION_CHARS = 500


@dataclass(frozen=True)
class ApprovalNeed:
    action_type: str
    description: str
    risk_level: str


def _summarise(name: str, arguments: dict[str, Any]) -> str:
    """A bounded, human-readable account of what the call is about to do."""
    for key in ("command", "path", "title", "name", "reference", "action"):
        value = arguments.get(key)
        if value:
            text = str(value)
            break
    else:
        text = json.dumps(arguments, default=str, sort_keys=True)
    text = f"{name}: {text}"
    if len(text) > MAX_DESCRIPTION_CHARS:
        return text[: MAX_DESCRIPTION_CHARS - 1] + "…"
    return text


def _classify(name: str, arguments: dict[str, Any]) -> tuple[str, str]:
    """(kind, risk_level) of a tool call; unknown tools are treated as commands."""
    if name in READ_TOOLS:
        return "read", "low"
    if name in EDIT_TOOLS:
        return "edit", "medium"
    if name == "git_revert" and str(arguments.get("mode", "files")) == "hard":
        return "publish", "critical"
    if name in PUBLISH_TOOLS:
        return "publish", "high"
    return "command", "medium"


def approval_needed(mode: str, name: str, arguments: dict[str, Any]) -> ApprovalNeed | None:
    """The human gate ``mode`` raises before this call runs, or ``None`` to run it."""
    kind, risk = _classify(name, arguments)
    if kind == "read":
        return None
    gated = (
        (kind == "edit" and mode in MODES_GATING_EDITS)
        or (kind == "command" and mode in MODES_GATING_COMMANDS)
        or (kind == "publish" and mode in MODES_GATING_COMMANDS)
    )
    if not gated:
        return None
    return ApprovalNeed(action_type=name, description=_summarise(name, arguments), risk_level=risk)


def changes_workspace(name: str, arguments: dict[str, Any]) -> bool:
    """Whether the call may modify the workspace, the repository or the outside world."""
    return _classify(name, arguments)[0] != "read"
