"""Two-Tier FastMCP Tool Registry managing In-Process (Tier 1) and Sandbox (Tier 2) tools."""

from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp import FastMCP

from agent_core.context import current_execution_ctx
from agent_core.tools import tier1, tier2
from specifications.interfaces.agent import AgentEvent, FileDiff, SubagentTaskRequest
from specifications.interfaces.llm import ToolDefinition
from specifications.interfaces.sandbox import SandboxDriverProtocol


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
        self._mcp: FastMCP = FastMCP("rocket-chat-tools")
        self._register_tools()

    def bind_subagent_runner(self, runner: Any) -> None:
        """Bind subagent runner for direct contextvars-driven subagent delegation."""
        self._subagent_runner = runner

    def bind_sandbox(self, driver: SandboxDriverProtocol, session_id: str) -> None:
        """Bind or update sandbox driver context for Tier 2 tool dispatches."""
        self._driver = driver
        self._session_id = session_id

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

    def _register_tools(self) -> None:
        # Tier 1: In-Process Tools
        @self._mcp.tool()
        async def web_fetch(url: str, timeout_seconds: float = 15.0) -> str:
            """Fetch content from a URL and convert HTML pages into clean Markdown."""
            return await tier1.web_fetch(url, timeout_seconds)

        @self._mcp.tool()
        async def web_search(query: str, max_results: int = 5) -> str:
            """Perform a web search and return structured title, URL, and snippet results."""
            return await tier1.web_search(query, max_results)

        @self._mcp.tool()
        async def ask_question(
            question: str,
            options: list[str],
            is_multi_select: bool = False,
            default_recommended_option: str | None = None,
        ) -> str:
            """Ask the human user an interactive question to clarify intent or select options."""
            if self._on_question_callback:
                return await self._on_question_callback(
                    question, options, is_multi_select, default_recommended_option
                )
            return f"Question submitted: {question} (Options: {options})"

        @self._mcp.tool()
        async def session_rename(title: str) -> str:
            """Rename the current session with a concise, descriptive title (3-6 words)."""
            ctx = current_execution_ctx.get()
            target_session_id = ctx.session_id if ctx else self._session_id
            if self._on_rename_callback and target_session_id:
                await self._on_rename_callback(target_session_id, title)
                return f"Session successfully renamed to: {title}"
            return f"Session title update requested: {title}"

        @self._mcp.tool()
        async def update_task_checklist(tasks: list[dict[str, Any]]) -> str:
            """Update or initialize the structured checklist of tasks for the session to track progress.
            Each task in 'tasks' must be a dict with:
            - 'id': unique string identifier (e.g. '1', 'setup')
            - 'title': concise task summary
            - 'status': one of 'pending', 'in_progress', 'completed', 'failed'
            - 'description': optional details
            """
            ctx = current_execution_ctx.get()
            if ctx and ctx.event_queue:
                ctx.event_queue.put_nowait(
                    AgentEvent(
                        event_type="plan_updated",
                        session_id=ctx.session_id,
                        payload={"session_id": ctx.session_id, "tasks": tasks},
                    )
                )
            if self._on_plan_callback:
                self._on_plan_callback(tasks)
            completed = sum(1 for t in tasks if t.get("status") == "completed")
            return f"Task checklist updated: {completed}/{len(tasks)} tasks completed."

        @self._mcp.tool()
        async def delegate_subagent(role: str, task: str, context: str = "") -> str:
            """Delegate a specialized sub-task to an autonomous specialist subagent (e.g. security_auditor, qa_verifier, researcher)."""
            ctx = current_execution_ctx.get()
            if self._subagent_runner and ctx:
                sub_req = SubagentTaskRequest(role=role, task=task, context=context)
                sub_res = await self._subagent_runner.run(
                    request=sub_req,
                    session_id=ctx.session_id,
                    event_queue=ctx.event_queue,
                    default_model=ctx.parent_model,
                )
                return (
                    f"[{sub_res.role.upper()} SUBAGENT REPORT]\n"
                    f"Task: {sub_res.task}\n"
                    f"Status: {sub_res.status} ({sub_res.turns_taken} turns, {sub_res.tool_calls_count} tools, {sub_res.duration_ms}ms)\n\n"
                    f"{sub_res.summary}"
                )
            if self._on_subagent_callback:
                return await self._on_subagent_callback(role, task, context)
            return f"Subagent delegation requested for role '{role}': {task}"

        # Tier 2: Sandbox Tools
        @self._mcp.tool()
        async def bash_exec(command: str, timeout_seconds: int = 120) -> str:
            """Execute a shell command inside the active sandbox environment."""
            ctx = current_execution_ctx.get()
            s_id = ctx.session_id if ctx else self._session_id
            if not self._driver or not s_id:
                return "Error: Sandbox not initialized for bash_exec"
            return await tier2.bash_exec(self._driver, s_id, command, timeout_seconds)

        @self._mcp.tool()
        async def file_read(
            path: str, start_line: int | None = None, end_line: int | None = None
        ) -> str:
            """Read file content with optional 1-indexed start and end line ranges."""
            ctx = current_execution_ctx.get()
            s_id = ctx.session_id if ctx else self._session_id
            if not self._driver or not s_id:
                return "Error: Sandbox not initialized for file_read"
            return await tier2.file_read(self._driver, s_id, path, start_line, end_line)

        @self._mcp.tool()
        async def file_write(path: str, content: str, overwrite: bool = True) -> str:
            """Write or overwrite file contents in the sandbox workspace."""
            ctx = current_execution_ctx.get()
            s_id = ctx.session_id if ctx else self._session_id
            if not self._driver or not s_id:
                return "Error: Sandbox not initialized for file_write"
            return await tier2.file_write(self._driver, s_id, path, content, overwrite)

        @self._mcp.tool()
        async def file_edit(path: str, target_block: str, replacement_block: str) -> str:
            """Perform resilient block replacement on a file in the sandbox workspace."""
            ctx = current_execution_ctx.get()
            s_id = ctx.session_id if ctx else self._session_id
            if not self._driver or not s_id:
                return "Error: Sandbox not initialized for file_edit"
            msg, diff = await tier2.file_edit(
                self._driver, s_id, path, target_block, replacement_block
            )
            if diff is not None:
                if ctx and ctx.event_queue:
                    from dataclasses import asdict

                    ctx.event_queue.put_nowait(
                        AgentEvent(
                            event_type="file_diff",
                            session_id=ctx.session_id,
                            payload=asdict(diff),
                        )
                    )
                if self._on_diff_callback:
                    self._on_diff_callback(diff)
            return msg

        @self._mcp.tool()
        async def apply_patch(path: str, patch_content: str) -> str:
            """Apply a unified diff patch to a file in the workspace."""
            ctx = current_execution_ctx.get()
            s_id = ctx.session_id if ctx else self._session_id
            if not self._driver or not s_id:
                return "Error: Sandbox not initialized for apply_patch"
            return await tier2.apply_patch(self._driver, s_id, path, patch_content)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name with arguments through FastMCP dispatch."""
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
            ToolDefinition(
                name="web_fetch",
                description="Fetch content from a URL and convert HTML pages into clean Markdown.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The HTTP or HTTPS URL to fetch.",
                        },
                        "timeout_seconds": {
                            "type": "number",
                            "description": "Request timeout in seconds (default: 15.0).",
                        },
                    },
                    "required": ["url"],
                },
            ),
            ToolDefinition(
                name="web_search",
                description=(
                    "Perform a web search and return structured title, URL, and snippet results."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query terms.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of search results to return (default: 5).",
                        },
                    },
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="ask_question",
                description=(
                    "Ask the human user an interactive question to clarify intent or select options."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The clarification question text.",
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of selectable options for the user.",
                        },
                        "is_multi_select": {
                            "type": "boolean",
                            "description": "Whether multiple options can be chosen (default: false).",
                        },
                        "default_recommended_option": {
                            "type": "string",
                            "description": "Recommended default choice.",
                        },
                    },
                    "required": ["question", "options"],
                },
            ),
            ToolDefinition(
                name="session_rename",
                description=(
                    "Rename the current session with a concise, descriptive title (3-6 words) reflecting the topic."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "The new concise, descriptive title for the session.",
                        },
                    },
                    "required": ["title"],
                },
            ),
            ToolDefinition(
                name="update_task_checklist",
                description=(
                    "Update or initialize the structured checklist of tasks for the session to track multi-step progress."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "description": "List of tasks in the checklist.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {
                                        "type": "string",
                                        "description": "Unique task identifier (e.g. '1', 'setup', 'test').",
                                    },
                                    "title": {
                                        "type": "string",
                                        "description": "Short human-readable title of the task.",
                                    },
                                    "status": {
                                        "type": "string",
                                        "enum": ["pending", "in_progress", "completed", "failed"],
                                        "description": "Current status of the task.",
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": "Optional brief details or context.",
                                    },
                                },
                                "required": ["id", "title", "status"],
                            },
                        },
                    },
                    "required": ["tasks"],
                },
            ),
            ToolDefinition(
                name="bash_exec",
                description="Execute a shell command inside the active sandbox environment.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to execute.",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": "Execution timeout in seconds (default: 120).",
                        },
                    },
                    "required": ["command"],
                },
            ),
            ToolDefinition(
                name="file_read",
                description="Read file content with optional 1-indexed start and end line ranges.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to the file to read.",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "Optional 1-indexed start line.",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Optional 1-indexed end line.",
                        },
                    },
                    "required": ["path"],
                },
            ),
            ToolDefinition(
                name="file_write",
                description="Write or overwrite file contents in the sandbox workspace.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Target workspace path to create or overwrite.",
                        },
                        "content": {
                            "type": "string",
                            "description": "String content to write.",
                        },
                        "overwrite": {
                            "type": "boolean",
                            "description": "Whether to overwrite existing file (default: true).",
                        },
                    },
                    "required": ["path", "content"],
                },
            ),
            ToolDefinition(
                name="file_edit",
                description="Perform resilient block replacement in a workspace file.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Target workspace file path to edit.",
                        },
                        "target_block": {
                            "type": "string",
                            "description": "Exact or near-exact block of lines to replace.",
                        },
                        "replacement_block": {
                            "type": "string",
                            "description": "New replacement content.",
                        },
                    },
                    "required": ["path", "target_block", "replacement_block"],
                },
            ),
            ToolDefinition(
                name="apply_patch",
                description="Apply a unified diff patch to a file in the workspace.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Target workspace file path to patch.",
                        },
                        "patch_content": {
                            "type": "string",
                            "description": "Unified diff patch content.",
                        },
                    },
                    "required": ["path", "patch_content"],
                },
            ),
            ToolDefinition(
                name="delegate_subagent",
                description="Delegate an isolated investigation or task to a specialist subagent (e.g. security_auditor, qa_verifier, researcher).",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "role": {
                            "type": "string",
                            "description": "Specialist role to spawn: 'security_auditor', 'qa_verifier', or 'researcher'.",
                        },
                        "task": {
                            "type": "string",
                            "description": "Clear, specific objective for the subagent to accomplish.",
                        },
                        "context": {
                            "type": "string",
                            "description": "Optional background hints or specific files to inspect.",
                        },
                    },
                    "required": ["role", "task"],
                },
            ),
        ]
