"""The sandbox MCP: the platform's sandboxes as tools for agents that run elsewhere.

A member's agent (Claude Code, Cursor, an in-house agent) connects with a personal API token,
opens a sandbox of a type from the organization's catalogue and calls the platform's own sandbox,
git and GitHub tools in it. Each sandbox is an ordinary session (``SessionSource.MCP``), so it
keeps everything a session has: the type's image, resources and variables, idle hibernation and
resume, workspace release, the git gateway and the organization's git policy.

It is a tool surface, never an agent: no call made through it reaches a model. The calls run
under ``ModelScope.call_role == MODEL_FREE_CALL_ROLE``, which the gateway and the embedding
helper refuse, and the exposed tools are the model-free ones listed in ``SANDBOX_MCP_TOOLS``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Where the API serves the MCP endpoint (streamable HTTP).
SANDBOX_MCP_PATH = "/v1/sandbox-mcp"

# The argument every proxied tool takes in addition to its own: the sandbox it acts in.
SANDBOX_ID_ARGUMENT = "sandbox_id"

# The platform tools the MCP proxies, by domain. Left out on purpose: tools that call a model or
# an embedding (code_search, knowledge and memory), that wake an agent turn later (github_watch,
# wait_for), that only make sense inside an agent loop (questions, checklist, plan mode,
# subagents, artifacts) and the browser and image tools, whose results are for a model's eyes.
SANDBOX_MCP_TOOLS: tuple[str, ...] = (
    # Repositories
    "clone_repository",
    "set_active_repository",
    "workspace_status",
    "git_status_all",
    # Files
    "file_read",
    "file_write",
    "file_edit",
    "apply_patch",
    "list_dir",
    "grep_search",
    "glob_files",
    # Commands
    "bash_exec",
    "package_manager",
    "run_tests",
    "lint",
    "typecheck",
    "build",
    # Environment
    "env_set",
    "env_unset",
    "env_list",
    # Background processes
    "process_start",
    "process_list",
    "process_output",
    "process_wait",
    "process_stop",
    # Git
    "git_status",
    "git_diff",
    "git_log",
    "git_commit",
    "git_branch",
    "git_checkpoint",
    "git_revert",
    "git_push",
    "git_open_pr",
    # GitHub
    "github_find_repositories",
    "github_read_file",
    "github_issues",
    "github_pull_request",
    "github_actions",
    "github_releases",
    "github_search",
    "github_api",
)


@dataclass(frozen=True)
class McpCaller:
    """Who calls the MCP: resolved from the request's credential, never from arguments."""

    org_id: str
    user_id: str
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class SandboxTypeSummary:
    """A sandbox type the caller may open: its variables by name only, never their values."""

    id: str
    name: str
    image: str
    description: str = ""
    is_default: bool = False
    capabilities: tuple[str, ...] = ()
    variables: tuple[str, ...] = ()


@dataclass(frozen=True)
class McpSandbox:
    """A sandbox opened through the MCP: the session that holds it."""

    sandbox_id: str
    title: str
    sandbox_type_id: str
    status: str
    created_at: int
    # The cockpit page of the session, relative to the deployment's web origin.
    cockpit_path: str


@dataclass(frozen=True)
class ToolCallOutcome:
    """A proxied tool's result, already redacted and bounded like any tool result."""

    text: str
    is_error: bool = False
    duration_ms: int = 0


class SandboxMcpError(Exception):
    """A refusal the caller can act on (disabled, unknown sandbox, busy, limit reached)."""


class SandboxMcpServiceProtocol(Protocol):
    """What the MCP transport asks of the platform; every method checks the caller's access."""

    async def sandbox_types(self, caller: McpCaller) -> list[SandboxTypeSummary]: ...

    async def open_sandbox(
        self, caller: McpCaller, sandbox_type_id: str | None, title: str | None
    ) -> McpSandbox: ...

    async def list_sandboxes(self, caller: McpCaller) -> list[McpSandbox]: ...

    async def close_sandbox(self, caller: McpCaller, sandbox_id: str) -> bool: ...

    async def call_tool(
        self, caller: McpCaller, sandbox_id: str, name: str, arguments: dict[str, object]
    ) -> ToolCallOutcome: ...
