"""A short status line for a step that is about to run tools.

The cockpit and Slack show it as "what the agent is doing now". It is derived from the tool
names, never from the model's own text: that text is already in the conversation as the
step's preface, and arguments (paths, commands) can carry secrets the status must not echo.
"""

from __future__ import annotations

from typing import Any

_VERBS: dict[str, str] = {
    "bash_exec": "Running a command",
    "process_start": "Starting a process",
    "repo_foreach": "Running a command in each repository",
    "run_tests": "Running tests",
    "lint": "Linting",
    "typecheck": "Type-checking",
    "build": "Building",
    "package_manager": "Managing packages",
    "file_read": "Reading files",
    "list_dir": "Listing files",
    "glob_files": "Finding files",
    "grep_search": "Searching the code",
    "code_search": "Searching the code",
    "file_edit": "Editing files",
    "file_write": "Writing files",
    "apply_patch": "Applying a patch",
    "apply_diff_file": "Applying a patch",
    "web_search": "Searching the web",
    "web_fetch": "Reading a web page",
    "git_status": "Checking git status",
    "git_diff": "Reading the diff",
    "git_commit": "Committing",
    "git_push": "Pushing",
    "git_open_pr": "Opening a pull request",
    "delegate_subagent": "Delegating to a subagent",
    "delegate_subagents_parallel": "Delegating to subagents",
    "update_task_checklist": "Updating the checklist",
    "ask_question": "Asking a question",
    "request_approval": "Requesting approval",
    "wait_for": "Waiting",
    "think": "Thinking",
}

# More distinct actions than this read as noise in a one-line indicator.
_MAX_LISTED = 2


def _readable(name: str) -> str:
    """``mcp__signoz__search_logs`` -> "Using search logs (signoz)"."""
    server = ""
    if name.startswith("mcp__") and "__" in name[5:]:
        server, name = name[5:].split("__", 1)
    words = name.replace("_", " ").replace("-", " ").strip() or "a tool"
    return f"Using {words} ({server})" if server else f"Using {words}"


def describe_step(calls: dict[int, dict[str, Any]]) -> str:
    """One line for the tools a step runs, in call order and without repeats."""
    labels: list[str] = []
    for index in sorted(calls):
        name = str(calls[index].get("name") or "")
        label = _VERBS.get(name) or _readable(name)
        if label not in labels:
            labels.append(label)
    if not labels:
        return ""
    shown, hidden = labels[:_MAX_LISTED], len(labels) - _MAX_LISTED
    line = ", ".join(shown)
    return f"{line} and {hidden} more..." if hidden > 0 else f"{line}..."
