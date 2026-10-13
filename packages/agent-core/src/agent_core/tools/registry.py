"""Two-Tier FastMCP Tool Registry managing In-Process (Tier 1) and Sandbox (Tier 2) tools."""

import difflib
import json
import logging
from collections.abc import Awaitable, Callable
from fnmatch import fnmatch
from typing import Any, ClassVar

from fastmcp import FastMCP
from fastmcp.tools.function_tool import FunctionTool

from agent_core.context import current_execution_ctx
from agent_core.permissions import approval_needed, changes_workspace
from agent_core.tools import git_tools, memory_tools
from agent_core.tools.builtin import register_all
from agent_core.tools.schema import definition_from_tool
from specifications.interfaces.agent import (
    APPROVAL_GRANTED_PREFIX,
    DEFAULT_PERMISSION_MODE,
    ExternalToolProvider,
    FileDiff,
    GithubAccess,
    GitPolicy,
)
from specifications.interfaces.llm import ToolDefinition
from specifications.interfaces.sandbox import SandboxDriverProtocol

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Two-tier tool registry registering FastMCP tools for LLM agent execution."""

    def __init__(
        self,
        driver: SandboxDriverProtocol | None = None,
        session_id: str | None = None,
    ) -> None:
        self._driver: SandboxDriverProtocol | None = driver
        self._session_id: str | None = session_id
        self._subagent_runner: Any = None
        self._on_diff_callback: Callable[[FileDiff], None] | None = None
        self._on_question_callback: (
            Callable[[str, list[str], bool, str | None], Awaitable[str]] | None
        ) = None
        self._on_rename_callback: Callable[[str, str], Awaitable[None]] | None = None
        self._on_plan_callback: Callable[[list[dict[str, Any]]], None] | None = None
        self._on_subagent_callback: Callable[[str, str, str], Awaitable[str]] | None = None
        self._on_kb_search_callback: Callable[[str, str | None, int], Awaitable[str]] | None = None
        self._on_execute_skill_callback: Callable[[str, dict[str, Any]], Awaitable[str]] | None = (
            None
        )
        self._on_ensure_sandbox_callback: Callable[[str], Awaitable[None]] | None = None
        self._on_approval_callback: Callable[[str, str, str], Awaitable[str]] | None = None
        self._on_code_search_callback: Callable[[str, int], Awaitable[str]] | None = None
        self._on_memory_remember_callback: Callable[[str, str, str], Awaitable[str]] | None = None
        self._on_memory_recall_callback: Callable[[str, str | None, int], Awaitable[str]] | None = (
            None
        )
        self._git_engine: Any = None
        self._dynamic_tools: dict[str, ToolDefinition] = {}
        # Optional toolset tag per dynamic tool. A tagged tool is only visible/callable for
        # a turn that enables that toolset (e.g. "slack"), so transport capabilities cannot
        # leak into sessions that did not opt in.
        self._dynamic_tool_toolsets: dict[str, str] = {}
        self._mcp: FastMCP = FastMCP("rocket-chat-tools")
        self._builtin_tools: dict[str, FunctionTool] = {}
        self._register_tools()

    # Tools that remain permitted while the session is in read-only plan mode.
    PLAN_READ_ONLY_TOOLS: frozenset[str] = frozenset(
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
            "clone_repository",
            "github_find_repositories",
            "github_read_file",
            "github_issues",
            "recall",
            "web_fetch",
            "web_search",
            "browser_inspect",
            "view_image",
            "update_task_checklist",
            "update_status",
            "ask_question",
            "session_rename",
            "think",
            "exit_plan_mode",
        }
    )

    def _turn_callback(self, name: str) -> Any:
        """The per-turn collaborator ``name`` for the executing turn.

        Turn-scoped callbacks live on the turn's ``ExecutionContext`` (so concurrent
        sessions can never see each other's approvals, questions or sandboxes). The
        ``set_*_callback`` setters remain as a process-wide fallback for single-session
        embeddings and tests.
        """
        ctx = current_execution_ctx.get()
        scoped = getattr(ctx.callbacks, name, None) if ctx is not None else None
        return scoped or getattr(self, f"_on_{name}_callback", None)

    def set_approval_callback(self, cb: Callable[[str, str, str], Awaitable[str]]) -> None:
        """Register an async callback invoked to request human approval."""
        self._on_approval_callback = cb

    def set_code_search_callback(self, cb: Callable[[str, int], Awaitable[str]]) -> None:
        """Register an async callback for semantic (indexed) code search."""
        self._on_code_search_callback = cb

    def bind_git_engine(self, engine: Any) -> None:
        """Bind a GitEngine used by commit/PR tools for signing and co-authorship."""
        self._git_engine = engine

    def set_kb_search_callback(self, cb: Callable[[str, str | None, int], Awaitable[str]]) -> None:
        """Register an async callback for pgvector knowledge base searches."""
        self._on_kb_search_callback = cb

    def set_execute_skill_callback(
        self, cb: Callable[[str, dict[str, Any]], Awaitable[str]]
    ) -> None:
        """Register an async callback for dynamic custom Python skill execution."""
        self._on_execute_skill_callback = cb

    def set_memory_callbacks(
        self,
        *,
        remember: Callable[[str, str, str], Awaitable[str]],
        recall: Callable[[str, str | None, int], Awaitable[str]],
    ) -> None:
        """Bind the tenant-scoped long-term-memory callbacks."""
        self._on_memory_remember_callback = remember
        self._on_memory_recall_callback = recall

    async def build_memory_context(self, query: str) -> str:
        """Return recalled memories for the dynamic prompt suffix, or ``""``.

        Prompt injection must never break a turn: any failure (no callback, database
        outage) degrades to "no memories" and is logged, keeping the stable cached prefix
        and the turn itself intact.
        """
        if self._on_memory_recall_callback is None:
            return ""
        try:
            return await self._on_memory_recall_callback(
                query, None, memory_tools.DEFAULT_RECALL_LIMIT
            )
        except Exception:
            logger.exception("Recalled-memory injection failed")
            return ""

    def register_dynamic_tool(
        self,
        definition: ToolDefinition,
        handler: Callable[..., Awaitable[Any]],
        *,
        toolset: str | None = None,
    ) -> None:
        """Register an external or tenant-defined tool schema and handler dynamically.

        ``toolset`` tags the tool as available only when a turn enables that toolset via
        ``ExecutionContext.enabled_toolsets`` (e.g. the ``slack`` toolset). Untagged tools
        remain globally available.
        """
        self._dynamic_tools[definition.name] = definition
        if toolset is not None:
            self._dynamic_tool_toolsets[definition.name] = toolset
        else:
            self._dynamic_tool_toolsets.pop(definition.name, None)
        self._mcp.tool(name=definition.name, description=definition.description)(handler)

    def _enabled_toolsets(self) -> frozenset[str]:
        ctx = current_execution_ctx.get()
        return ctx.enabled_toolsets if ctx is not None else frozenset()

    def _toolset_blocked(self, name: str) -> bool:
        tag = self._dynamic_tool_toolsets.get(name)
        return tag is not None and tag not in self._enabled_toolsets()

    def visible_dynamic_tools(self) -> list[ToolDefinition]:
        """Dynamic tools visible to the current turn (toolset-scoped)."""
        enabled = self._enabled_toolsets()
        return [
            definition
            for name, definition in self._dynamic_tools.items()
            if (tag := self._dynamic_tool_toolsets.get(name)) is None or tag in enabled
        ]

    def bind_subagent_runner(self, runner: Any) -> None:
        """Bind subagent runner for direct contextvars-driven subagent delegation."""
        self._subagent_runner = runner

    def bind_sandbox(self, driver: SandboxDriverProtocol, session_id: str) -> None:
        """Bind or update sandbox driver context for Tier 2 tool dispatches."""
        self._driver = driver
        self._session_id = session_id

    def set_ensure_sandbox_callback(self, cb: Callable[[str], Awaitable[None]]) -> None:
        """Register an async callback invoked on-demand to provision/start sandbox when Tier 2 tools execute."""
        self._on_ensure_sandbox_callback = cb

    async def _processes_changed(self, session_id: str) -> None:
        changed = self._turn_callback("processes_changed")
        if changed:
            await changed(session_id)

    async def _ensure_sandbox(self, session_id: str) -> None:
        ensure = self._turn_callback("ensure_sandbox")
        if ensure and session_id:
            await ensure(session_id)

    def set_diff_callback(self, cb: Callable[[FileDiff], None]) -> None:
        """Register a callback invoked whenever file modifications generate a FileDiff."""
        self._on_diff_callback = cb

    def set_question_callback(
        self, cb: Callable[[str, list[str], bool, str | None], Awaitable[str]]
    ) -> None:
        """Register an async callback invoked when an interactive decision gate is triggered."""
        self._on_question_callback = cb

    def set_rename_callback(self, cb: Callable[[str, str], Awaitable[None]]) -> None:
        """Register an async callback invoked when an agent renames the session."""
        self._on_rename_callback = cb

    def set_plan_callback(self, cb: Callable[[list[dict[str, Any]]], None]) -> None:
        """Register a callback invoked when the agent creates or updates the task checklist."""
        self._on_plan_callback = cb

    def set_subagent_callback(self, cb: Callable[[str, str, str], Awaitable[str]]) -> None:
        """Register an async callback invoked when delegating to a specialized subagent."""
        self._on_subagent_callback = cb

    @property
    def mcp(self) -> FastMCP:
        """Return the underlying FastMCP server instance."""
        return self._mcp

    def _tool(self, *, description: str) -> Callable[[Callable[..., Awaitable[str]]], FunctionTool]:
        """Register a built-in tool; its handler signature defines the model-facing schema."""

        def register(handler: Callable[..., Awaitable[str]]) -> FunctionTool:
            tool = FunctionTool.from_function(handler, description=description)
            self._mcp.add_tool(tool)
            self._builtin_tools[tool.name] = tool
            return tool

        return register

    def _register_tools(self) -> None:
        register_all(self)

    async def _github_access(self) -> GithubAccess:
        """The credential for this turn's read-only GitHub tools (public access without one)."""
        provider = self._turn_callback("github_access")
        return await provider() if provider else GithubAccess(token=None)

    @staticmethod
    def _git_policy() -> GitPolicy:
        """The organization git policy of the executing turn (defaults outside a turn)."""
        ctx = current_execution_ctx.get()
        return ctx.policy.git if ctx is not None else GitPolicy()

    def _sandbox_target(self, tool_name: str) -> tuple[SandboxDriverProtocol, str] | None:
        """Resolve the active (driver, session_id) for a Tier-2 tool, if bound."""
        ctx = current_execution_ctx.get()
        session_id = ctx.session_id if ctx else self._session_id
        if not self._driver or not session_id:
            return None
        return self._driver, session_id

    def _plan_mode_blocked(self, name: str) -> bool:
        """Whether a tool call must be rejected because the session is in plan mode."""
        ctx = current_execution_ctx.get()
        if not ctx or not ctx.plan_mode:
            return False
        return name not in self.PLAN_READ_ONLY_TOOLS

    async def _checkpoint_before_first_change(self, name: str, arguments: dict[str, Any]) -> None:
        """Snapshot the workspace before a turn first changes it, so any turn can be rewound.

        Best effort and only for git workspaces: a failure here must never stop the work the
        user asked for.
        """
        ctx = current_execution_ctx.get()
        target = self._sandbox_target(name)
        if (
            ctx is None
            or ctx.checkpointed
            or target is None
            or not changes_workspace(name, arguments)
        ):
            return
        driver, session_id = target
        try:
            await self._ensure_sandbox(session_id)
            if await git_tools.is_git_workspace(driver, session_id):
                await git_tools.git_checkpoint(driver, session_id, label="turn")
            ctx.checkpointed = True
        except Exception as err:
            logger.debug("Skipping automatic checkpoint for %s: %s", session_id, err)

    async def _permission_refusal(self, name: str, arguments: dict[str, Any]) -> str | None:
        """Raise the human gate the session's permission mode requires; the refusal if denied.

        Fails closed: a mode that needs a human but has no approval channel (a headless
        embedding) refuses the call instead of running it unsupervised.
        """
        ctx = current_execution_ctx.get()
        mode = ctx.policy.permission_mode if ctx is not None else DEFAULT_PERMISSION_MODE
        need = approval_needed(mode, name, arguments)
        if need is None:
            return None
        approve = self._turn_callback("approval")
        if approve is None:
            return (
                f"Blocked: '{name}' needs human approval in '{mode}' mode, but this session "
                "has no approval channel."
            )
        verdict = str(await approve(need.action_type, need.description, need.risk_level))
        return None if verdict.startswith(APPROVAL_GRANTED_PREFIX) else verdict

    # Tools that only work on a sandbox image that declares the capability.
    CAPABILITY_TOOLS: ClassVar[dict[str, str]] = {"browser_inspect": "browser"}

    def _capability_missing(self, name: str) -> str | None:
        """The capability this tool needs but the session's sandbox does not declare."""
        ctx = current_execution_ctx.get()
        needed = self.CAPABILITY_TOOLS.get(name)
        if ctx is None or needed is None or ctx.sandbox_capabilities is None:
            return None
        return None if needed in ctx.sandbox_capabilities else needed

    def _scope_tool_blocked(self, name: str) -> bool:
        """Whether the current execution scope forbids this tool (e.g. Slack allowlist)."""
        ctx = current_execution_ctx.get()
        if ctx is None or ctx.allowed_tools is None:
            return False
        return name not in ctx.allowed_tools

    def _is_known_tool(self, name: str) -> bool:
        provider = self._external_provider()
        return (
            name in self._builtin_tools
            or name in self._dynamic_tools
            or (provider is not None and provider.owns(name))
        )

    def unknown_tool_message(self, name: str) -> str:
        """Tell the model the tool does not exist, what is close, and how to find the right one.

        A bare "Unknown tool" invites the model to retry the same name or guess another one;
        naming the nearest real tools and the discovery tool ends that loop.
        """
        available = sorted(definition.name for definition in self.get_tool_definitions())
        close = difflib.get_close_matches(name, available, n=3, cutoff=0.5)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        return (
            f"Error: there is no tool named '{name}'.{hint} Do not call it again. "
            "Call `search_tools` (optionally with a keyword) to list the tools you can use, "
            "and only call a name it returns."
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name, enforcing the plan-mode and scope-allowlist gates."""
        # First, so a made-up name is never offered to a human for approval as a "command".
        if not self._is_known_tool(name):
            return self.unknown_tool_message(name)
        if self._plan_mode_blocked(name):
            return (
                f"Blocked: '{name}' is not permitted while the session is in plan mode "
                "(read-only). Finish investigating, then call `exit_plan_mode` to request "
                "human approval before making any changes."
            )
        if missing := self._capability_missing(name):
            return (
                f"Blocked: '{name}' needs a sandbox with the '{missing}' capability, and this "
                "session's sandbox does not have it. Ask the user to choose or create a sandbox "
                f"with '{missing}' enabled (Settings > Organization > Sandboxes)."
            )
        if self._scope_tool_blocked(name):
            # Enforced, not advisory: an unauthenticated Slack caller cannot reach a tool
            # the workspace restricted, even if the model asks for it directly.
            return (
                f"Blocked: the tool '{name}' is not permitted in this Slack workspace. "
                "The workspace administrator restricted it for unauthenticated callers; "
                "ask them to allow it, or use the web cockpit where the full toolset is "
                "available."
            )
        if self._toolset_blocked(name):
            return (
                f"Blocked: the tool '{name}' belongs to a toolset that is not enabled for "
                "this session. Enable the toolset on the session or agent before use."
            )
        refusal = await self._permission_refusal(name, arguments)
        if refusal is not None:
            return refusal
        blocked = await self._run_hooks("pre_tool", name, arguments)
        if blocked is not None:
            return f"Blocked by an organization hook: {blocked}"
        await self._checkpoint_before_first_change(name, arguments)
        result = await self._dispatch(name, arguments)
        follow_up = await self._run_hooks("post_tool", name, arguments, result=result)
        return f"{result}\n\n[post_tool hook failed] {follow_up}" if follow_up else result

    async def _run_hooks(
        self, event: str, name: str, arguments: dict[str, Any], result: str | None = None
    ) -> str | None:
        """Run the organization's matching hooks in the sandbox; the first failure's output.

        The tool name and arguments reach the command through its environment, never spliced
        into the command line, so a model-chosen argument cannot alter what a hook executes.
        """
        ctx = current_execution_ctx.get()
        target = self._sandbox_target(name)
        if ctx is None or target is None:
            return None
        hooks = [h for h in ctx.policy.hooks if h.event == event and fnmatch(name, h.match)]
        if not hooks:
            return None
        driver, session_id = target
        await self._ensure_sandbox(session_id)
        env = {
            "ROCKET_HOOK_EVENT": event,
            "ROCKET_TOOL_NAME": name,
            "ROCKET_TOOL_ARGS": json.dumps(arguments, default=str)[:4000],
        }
        if result is not None:
            env["ROCKET_TOOL_RESULT"] = result[:4000]
        for hook in hooks:
            try:
                outcome = await driver.exec_command(
                    session_id, hook.command, timeout_seconds=hook.timeout_seconds, env=env
                )
            except Exception as err:
                return f"hook '{hook.command}' could not run: {err}"
            if outcome.exit_code != 0:
                detail = (outcome.stdout + outcome.stderr).strip()[-1500:]
                return f"hook '{hook.command}' exited {outcome.exit_code}: {detail}"
        return None

    async def _dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        provider = self._external_provider()
        if provider is not None and provider.owns(name):
            return await provider.call(name, arguments)
        result = await self._mcp.call_tool(name, arguments)
        if hasattr(result, "content") and result.content:
            first = result.content[0]
            if hasattr(first, "text"):
                return str(first.text)
        if hasattr(result, "structured_content") and result.structured_content:
            structured = result.structured_content.get("result")
            if structured is not None:
                return str(structured)
        return str(result)

    def get_tool_definitions(self) -> list[ToolDefinition]:
        """Return ToolDefinitions formatted for ModelRequest consumption."""
        return [
            *(
                definition_from_tool(tool)
                for name, tool in self._builtin_tools.items()
                if self._capability_missing(name) is None
            ),
            *self.visible_dynamic_tools(),
            *self._external_definitions(),
        ]

    def _external_provider(self) -> ExternalToolProvider | None:
        ctx = current_execution_ctx.get()
        return ctx.callbacks.external_tools if ctx is not None else None

    def _external_definitions(self) -> list[ToolDefinition]:
        provider = self._external_provider()
        return provider.definitions() if provider is not None else []

    def catalog(self) -> list[ToolDefinition]:
        """Every tool the registry can expose, *including* toolset-scoped ones.

        Unlike ``get_tool_definitions`` this is not filtered by the current turn's enabled
        toolsets, so the admin catalog UI can show Slack/MCP tools too.
        """
        visible = self.get_tool_definitions()
        seen = {definition.name for definition in visible}
        extras = [
            definition for definition in self._dynamic_tools.values() if definition.name not in seen
        ]
        return [*visible, *extras]

    def toolset_of(self, name: str) -> str | None:
        """The toolset a dynamic tool belongs to, if any (e.g. ``"slack"``)."""
        return self._dynamic_tool_toolsets.get(name)
