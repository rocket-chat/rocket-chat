"""Permission modes: how much of a turn's work waits for a human.

The model can always ask for approval itself (``request_approval``), but supervision that
depends on the model remembering to ask is not supervision. A mode makes the platform ask:
every tool call is classified and, depending on the session's mode, a human gate is raised
*before* the tool runs. The decision then flows through the same approval events and the
organization's approval policy as a model-initiated request.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent_core.edit_preview import CUT_NOTICE, clamp_text, edit_preview
from agent_core.tools.github_ci import ACTIONS_READ, RELEASES_READ
from agent_core.tools.github_generic import call_is_critical, call_is_read
from agent_core.tools.github_prs import PR_READ_ACTIONS
from specifications.interfaces.agent import (
    APPROVAL_RULE_SCOPES,
    MAX_APPROVAL_ARGUMENT_CHARS,
    MAX_APPROVAL_ARGUMENTS_CHARS,
    MAX_APPROVAL_DIFF_CHARS,
    ApprovalDetails,
    ApprovalRule,
    RememberedApprovals,
)

# The Slack toolset's reads: conversations, people, user groups and links.
SLACK_READ_TOOLS: frozenset[str] = frozenset(
    {
        "slack_read_thread",
        "slack_read_channel_history",
        "slack_list_conversations",
        "slack_resolve_user",
        "slack_get_user",
        "slack_list_user_groups",
        "slack_get_user_group_members",
        "slack_list_channel_members",
        "slack_get_permalink",
    }
)

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
        "load_tools",
        "load_guidance",
        # Fetching code to read it: it adds files to the sandbox but changes no repository.
        "clone_repository",
        "set_active_repository",
        # Looking at or waiting for a process the session itself started.
        "process_list",
        "process_output",
        "process_wait",
        # Read-only GitHub lookups: nothing in the sandbox, repository or account changes.
        "github_find_repositories",
        "github_read_file",
        "github_search",
        # Recording that the session follows a pull request; nothing on GitHub changes.
        "github_watch",
        "github_unwatch",
        *SLACK_READ_TOOLS,
        "recall",
        "web_fetch",
        "web_search",
        "browser_inspect",
        "view_image",
        "think",
        "update_task_checklist",
        "ask_question",
        "session_rename",
        "enter_plan_mode",
        "exit_plan_mode",
        "request_approval",
        "remember",
        "git_checkpoint",
        "delegate_subagent",
        "delegate_subagents_parallel",
        "subagent_status",
        "subagent_cancel",
        "workspace_status",
        "git_status_all",
        "env_list",
        "wait_for",
        "create_artifact",
        "update_artifact",
        "read_artifact",
        "list_artifacts",
    }
)
EDIT_TOOLS: frozenset[str] = frozenset(
    {"file_write", "file_edit", "apply_patch", "apply_diff_file"}
)
# Edits that raise their own gate, with what the person must see (a merged branch's whole
# diff): plan mode refuses them like any edit, but no mode gates them a second time.
SELF_GATED_EDIT_TOOLS: frozenset[str] = frozenset({"merge_subagent_branch"})
# Publishing changes leaves the sandbox: commits, branches, pull requests.
# Messages and reactions in Slack reach other people: they are publications too.
PUBLISH_TOOLS: frozenset[str] = frozenset(
    {
        "git_commit", "git_branch", "git_push", "git_open_pr", "git_revert",
        "slack_send_message", "slack_send_dm", "slack_add_reaction",
    }
)  # fmt: skip

# GitHub tools whose calls are reads or changes depending on their action (or method).
_GITHUB_READ_ACTIONS: dict[str, frozenset[str]] = {
    "github_pull_request": PR_READ_ACTIONS,
    "github_actions": ACTIONS_READ,
    "github_releases": RELEASES_READ,
}
# Changes a person must approve in every mode but bypass: they cannot be taken back.
_CRITICAL_GITHUB_ACTIONS = frozenset({("github_pull_request", "merge")})
ACTION_CLASSIFIED_TOOLS = frozenset({*_GITHUB_READ_ACTIONS, "github_issues", "github_api"})
# Tools whose ``command`` runs in a shell, where a destructive command is critical.
_SHELL_TOOLS = frozenset({"bash_exec", "process_start", "repo_foreach"})
# Commands that wipe the workspace or the machine: no checkpoint brings them back. Deliberately
# narrow (absolute roots only): deleting a build directory, or `.` in one, is ordinary work.
_DESTRUCTIVE_COMMAND = re.compile(
    r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+(-[a-zA-Z]*\s+)*"
    r"(/|/\*|~|~/|\$HOME|/workspace|/workspace/|/workspace/\*)(\s|;|&|\||$)"
    r"|\bmkfs(\.\w+)?\b|\bdd\s+[^|;&]*\bof=/dev/"
)

MODES_GATING_EDITS: frozenset[str] = frozenset({"ask"})
MODES_GATING_COMMANDS: frozenset[str] = frozenset({"ask", "auto_edit"})
# Critical calls (irreversible: merges, deletions on GitHub, hard reverts, wiping the
# workspace) wait for a person in every mode but bypass.
MODES_SKIPPING_CRITICAL: frozenset[str] = frozenset({"bypass"})

MAX_DESCRIPTION_CHARS = 500


@dataclass(frozen=True)
class ApprovalNeed:
    action_type: str
    description: str
    risk_level: str


def _summarise(name: str, arguments: dict[str, Any]) -> str:
    """A bounded, human-readable account of what the call is about to do."""
    for key in ("command", "path", "title", "name", "reference", "action", "actions"):
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


def requested_actions(arguments: dict[str, Any]) -> list[str]:
    """The actions a call asks for: the batched ``actions`` list, or a single ``action``."""
    actions = arguments.get("actions")
    if isinstance(actions, list):
        return [str(action or "") for action in actions]
    if isinstance(actions, str) and actions:
        return [actions]
    return [str(arguments.get("action") or "")]


def _classify_action(name: str, action: str) -> tuple[str, str]:
    if name == "github_issues":
        return ("read", "low") if not action else ("publish", "high")
    if action in _GITHUB_READ_ACTIONS[name]:
        return "read", "low"
    return "publish", "critical" if (name, action) in _CRITICAL_GITHUB_ACTIONS else "high"


_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _classify_github(name: str, arguments: dict[str, Any]) -> tuple[str, str]:
    if name == "github_api":
        if call_is_read(arguments):
            return "read", "low"
        return "publish", "critical" if call_is_critical(arguments) else "high"
    # A batch is as risky as its riskiest action: one write makes it a publish, one merge
    # makes it critical.
    verdicts = [_classify_action(name, action) for action in requested_actions(arguments)]
    return max(verdicts, key=lambda verdict: _RISK_ORDER[verdict[1]])


def is_read_call(name: str, arguments: dict[str, Any]) -> bool:
    """Whether this call changes nothing (what plan mode allows)."""
    return _classify(name, arguments)[0] == "read"


def _classify(name: str, arguments: dict[str, Any]) -> tuple[str, str]:
    """(kind, risk_level) of a tool call; unknown tools are treated as commands."""
    if name in ACTION_CLASSIFIED_TOOLS:
        return _classify_github(name, arguments)
    if name in READ_TOOLS:
        return "read", "low"
    if name in EDIT_TOOLS or name in SELF_GATED_EDIT_TOOLS:
        return "edit", "medium"
    if name == "git_revert" and str(arguments.get("mode", "files")) == "hard":
        return "publish", "critical"
    if name in PUBLISH_TOOLS:
        return "publish", "high"
    if name in _SHELL_TOOLS and _DESTRUCTIVE_COMMAND.search(str(arguments.get("command") or "")):
        return "command", "critical"
    return "command", "medium"


def permission_class(name: str) -> str:
    """``read``, ``edit``, ``command`` or ``publish``: what a tool does with no arguments, which
    is how the agent editor groups it (a GitHub tool's actions may change it per call)."""
    return _classify(name, {})[0]


def approval_needed(mode: str, name: str, arguments: dict[str, Any]) -> ApprovalNeed | None:
    """The human gate ``mode`` raises before this call runs, or ``None`` to run it."""
    kind, risk = _classify(name, arguments)
    if kind == "read" or name in SELF_GATED_EDIT_TOOLS:
        return None
    gated = (
        (kind == "edit" and mode in MODES_GATING_EDITS)
        or (kind == "command" and mode in MODES_GATING_COMMANDS)
        or (kind == "publish" and mode in MODES_GATING_COMMANDS)
        or (risk == "critical" and mode not in MODES_SKIPPING_CRITICAL)
    )
    if not gated:
        return None
    return ApprovalNeed(action_type=name, description=_summarise(name, arguments), risk_level=risk)


def gated_call(name: str, arguments: dict[str, Any]) -> ApprovalNeed:
    """The gate of a tool the transport always gates (``TurnPolicy.gated_tools``)."""
    _, risk = _classify(name, arguments)
    return ApprovalNeed(
        action_type=name,
        description=_summarise(name, arguments),
        risk_level=risk if risk in ("high", "critical") else "high",
    )


def _normalised_command(arguments: Mapping[str, Any]) -> str:
    return " ".join(str(arguments.get("command") or "").split())


def rememberable_command(name: str, arguments: Mapping[str, Any]) -> str | None:
    """What a ``repository`` rule saved from these (approval card) arguments matches.

    ``""`` for a tool that runs no command (every call of the tool), the command for a shell
    tool, and ``None`` when the card only showed part of the command: a rule saved from a cut
    command would never match the real one.
    """
    if name not in _SHELL_TOOLS:
        return ""
    raw = str(arguments.get("command") or "")
    if not raw.strip() or raw.endswith(CUT_NOTICE.format(limit=MAX_APPROVAL_ARGUMENT_CHARS)):
        return None
    return _normalised_command(arguments)


def covering_rule(
    remembered: RememberedApprovals,
    need: ApprovalNeed,
    arguments: Mapping[str, Any],
    repository: str,
) -> ApprovalRule | None:
    """The remembered approval that answers this gate, if one does.

    A rule never answers a critical call, nor a risk level the platform left out of
    ``remembered.risks`` (the organization denies or auto-approves it, switched remembered
    approvals off, or locked the permission mode).
    """
    if need.risk_level == "critical" or need.risk_level not in remembered.risks:
        return None
    command = _normalised_command(arguments)
    for rule in remembered.rules:
        if rule.tool != need.action_type:
            continue
        if rule.command and rule.command != command:
            continue
        if rule.scope == "repository" and (not repository or rule.repository != repository):
            continue
        if rule.scope in APPROVAL_RULE_SCOPES:
            return rule
    return None


def _bounded_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The call's arguments as a person reads them: each long string cut, the whole bounded."""
    shown: dict[str, Any] = {}
    used = 0
    for key, value in arguments.items():
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        text = clamp_text(text, MAX_APPROVAL_ARGUMENT_CHARS)
        if used + len(text) > MAX_APPROVAL_ARGUMENTS_CHARS:
            shown["…"] = f"{len(arguments) - len(shown)} more arguments not shown"
            break
        shown[key] = text
        used += len(text)
    return shown


def approval_details(name: str, arguments: Mapping[str, Any], repository: str) -> ApprovalDetails:
    """What an approval card shows for this call: its arguments and, for an edit, the diff."""
    return ApprovalDetails(
        tool=name,
        arguments=_bounded_arguments(arguments),
        diff=edit_preview(name, arguments, MAX_APPROVAL_DIFF_CHARS),
        repository=repository,
    )


def touches_workspace(name: str, arguments: dict[str, Any]) -> bool:
    """Whether the call may change the sandbox's files or repositories: what a turn checkpoint
    protects. Publishing to Slack or GitHub and external (MCP) tools act elsewhere, so they
    must not start a sandbox only to snapshot it."""
    # A skill's code runs in a scratch directory and an instructions skill runs nothing; the
    # skill callback starts the sandbox itself when the skill has code.
    if name.startswith("mcp__") or name == "execute_skill":
        return False
    kind = _classify(name, arguments)[0]
    if kind == "publish":
        return name.startswith("git_")
    return kind in ("edit", "command")
