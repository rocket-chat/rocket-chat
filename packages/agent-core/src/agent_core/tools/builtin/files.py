"""Reading, editing and searching files in the workspace."""

import logging
from dataclasses import asdict, replace
from typing import TYPE_CHECKING, Annotated

from pydantic import Field
from sandbox_driver.workspace import WORKSPACE

from agent_core.context import current_execution_ctx
from agent_core.tools import code_search as code_search_tools
from agent_core.tools import tier2
from agent_core.tools.offload import get_cached_offload
from specifications.interfaces.agent import AgentEvent, FileDiff

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

RELATIVE_PATHS = "{}. Relative paths are inside the active repository; absolute ones as given."


def emit_file_diff(registry: "ToolRegistry", diff: FileDiff | None) -> None:
    """Tell the turn a file changed, once: through the turn's diff collaborator when it has
    one (it publishes the event), else on the turn's event queue."""
    if diff is None:
        return
    # Diff events name files relative to /workspace, whatever path the tool was given.
    diff = replace(diff, path=diff.path.removeprefix(f"{WORKSPACE}/"))
    on_diff = registry._turn_callback("diff")
    if on_diff:
        on_diff(diff)
        return
    ctx = current_execution_ctx.get()
    if ctx and ctx.event_queue:
        ctx.event_queue.put_nowait(
            AgentEvent(event_type="file_diff", session_id=ctx.session_id, payload=asdict(diff))
        )


def register(registry: "ToolRegistry") -> None:
    _register_file_editing_tools(registry)
    _register_patch_tools(registry)
    _register_file_search_tools(registry)


def _slice_cached_text(
    cached: str, start_line: int | None, end_line: int | None, line_numbers: bool
) -> str:
    lines = cached.splitlines()
    s = max(1, start_line or 1)
    e = min(len(lines), end_line or (s + 399))
    subset = lines[s - 1 : e]
    if line_numbers:
        numbered = [f"{i:4d} | {line}" for i, line in enumerate(subset, start=s)]
        return f"[lines {s}-{e} of {len(lines)}]\n" + "\n".join(numbered)
    return f"[lines {s}-{e} of {len(lines)}]\n" + "\n".join(subset)


def _register_file_editing_tools(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Read a file with line numbers, 400 lines at a time unless you give a range; the "
            "result says how many lines the file has and how to read on. The numbers are not "
            "part of the file: leave them out of file_edit blocks."
        )
    )
    async def file_read(
        path: Annotated[str, Field(description=RELATIVE_PATHS.format("File to read"))],
        start_line: Annotated[
            int | None, Field(description="Optional 1-indexed first line (default 1).")
        ] = None,
        end_line: Annotated[
            int | None, Field(description="Optional 1-indexed last line (default: 400 lines on).")
        ] = None,
        line_numbers: Annotated[
            bool, Field(description="Prefix each line with its number (default: true).")
        ] = True,
    ) -> str:
        ctx = current_execution_ctx.get()
        s_id = ctx.session_id if ctx else registry._session_id
        target = registry.workspace_file(path)
        cached = get_cached_offload(s_id, target) if s_id else None
        if not registry._driver or not s_id:
            if cached is not None:
                return _slice_cached_text(cached, start_line, end_line, line_numbers)
            return "Error: Sandbox not initialized for file_read"
        await registry._ensure_sandbox(s_id)
        if cached is not None and registry._driver:
            try:
                rel = target.removeprefix(f"{WORKSPACE}/").lstrip("/")
                await registry._driver.write_file(s_id, rel, cached, overwrite=False)
            except Exception:
                pass
        result = await tier2.file_read(
            registry._driver, s_id, target, start_line, end_line, line_numbers
        )
        return registry.explain_missing(path, result)

    @registry._tool(description="Write or overwrite file contents in the sandbox workspace.")
    async def file_write(
        path: Annotated[
            str, Field(description=RELATIVE_PATHS.format("File to create or overwrite"))
        ],
        content: Annotated[str, Field(description="String content to write.")],
        overwrite: Annotated[
            bool, Field(description="Whether to overwrite existing file (default: true).")
        ] = True,
    ) -> str:
        ctx = current_execution_ctx.get()
        s_id = ctx.session_id if ctx else registry._session_id
        if not registry._driver or not s_id:
            return "Error: Sandbox not initialized for file_write"
        await registry._ensure_sandbox(s_id)
        msg, diff = await tier2.file_write(
            registry._driver, s_id, registry.workspace_file(path), content, overwrite
        )
        emit_file_diff(registry, diff)
        return msg

    @registry._tool(description="Perform resilient block replacement in a workspace file.")
    async def file_edit(
        path: Annotated[str, Field(description=RELATIVE_PATHS.format("File to edit"))],
        target_block: Annotated[
            str, Field(description="Exact or near-exact block of lines to replace.")
        ],
        replacement_block: Annotated[str, Field(description="New replacement content.")],
    ) -> str:
        ctx = current_execution_ctx.get()
        s_id = ctx.session_id if ctx else registry._session_id
        if not registry._driver or not s_id:
            return "Error: Sandbox not initialized for file_edit"
        await registry._ensure_sandbox(s_id)
        msg, diff = await tier2.file_edit(
            registry._driver, s_id, registry.workspace_file(path), target_block, replacement_block
        )
        emit_file_diff(registry, diff)
        return registry.explain_missing(path, msg)


def _register_patch_tools(registry: "ToolRegistry") -> None:
    @registry._tool(description="Apply a unified diff patch to a file in the workspace.")
    async def apply_patch(
        path: Annotated[str, Field(description=RELATIVE_PATHS.format("File to patch"))],
        patch_content: Annotated[str, Field(description="Unified diff patch content.")],
    ) -> str:
        ctx = current_execution_ctx.get()
        s_id = ctx.session_id if ctx else registry._session_id
        if not registry._driver or not s_id:
            return "Error: Sandbox not initialized for apply_patch"
        await registry._ensure_sandbox(s_id)
        msg, diff = await tier2.apply_patch(
            registry._driver, s_id, registry.workspace_file(path), patch_content
        )
        emit_file_diff(registry, diff)
        return msg

    @registry._tool(
        description=(
            "Apply only one file's hunks from a multi-file unified diff to the "
            "workspace, after human approval. Use it to accept a single file of a "
            "proposed multi-file patch."
        )
    )
    async def apply_diff_file(
        diff_content: Annotated[
            str, Field(description="The full unified diff, possibly spanning several files.")
        ],
        path: Annotated[str, Field(description="Workspace-relative file to apply from the diff.")],
    ) -> str:
        target = registry._sandbox_target("apply_diff_file")
        if not target:
            return "Error: Sandbox not initialized for apply_diff_file"
        approve = registry._turn_callback("approval")
        if approve:
            decision = await approve(
                "apply_diff_file",
                f"Apply the patch section for '{path}' to the workspace.",
                "medium",
            )
            if not decision.startswith("Approved"):
                return f"Patch for '{path}' was not approved: {decision}"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        msg, diff = await tier2.apply_diff_file(driver, session_id, diff_content, path)
        emit_file_diff(registry, diff)
        return msg


def _register_file_search_tools(registry: "ToolRegistry") -> None:
    @registry._tool(
        description="List the contents of a directory in the sandbox workspace (long format)."
    )
    async def list_dir(
        path: Annotated[
            str, Field(description=RELATIVE_PATHS.format("Directory to list (default: '.')"))
        ] = ".",
    ) -> str:
        ctx = current_execution_ctx.get()
        s_id = ctx.session_id if ctx else registry._session_id
        if not registry._driver or not s_id:
            return "Error: Sandbox not initialized for list_dir"
        await registry._ensure_sandbox(s_id)
        result = await tier2.list_dir(registry._driver, s_id, registry.workspace_file(path))
        return registry.explain_missing(path, result)

    @registry._tool(
        description=(
            "Search file contents recursively for a regex pattern (ripgrep, with grep "
            "fallback). Prefer this over bash grep/find for repository navigation."
        )
    )
    async def grep_search(
        pattern: Annotated[str, Field(description="Regex/plain pattern to search for.")],
        path: Annotated[
            str, Field(description="Directory or file to search within (default: '.').")
        ] = ".",
        file_glob: Annotated[
            str | None, Field(description="Optional glob filter, e.g. '*.py' or 'src/**/*.ts'.")
        ] = None,
        case_sensitive: Annotated[
            bool, Field(description="Whether the search is case sensitive (default: true).")
        ] = True,
        max_results: Annotated[
            int, Field(description="Maximum matching lines to return (default: 200).")
        ] = 200,
    ) -> str:
        ctx = current_execution_ctx.get()
        s_id = ctx.session_id if ctx else registry._session_id
        if not registry._driver or not s_id:
            return "Error: Sandbox not initialized for grep_search"
        await registry._ensure_sandbox(s_id)
        return await tier2.grep_search(
            registry._driver,
            s_id,
            pattern,
            registry.workspace_file(path),
            file_glob,
            case_sensitive,
            max_results,
        )

    @registry._tool(
        description="Find workspace files matching a glob pattern (supports ** recursion)."
    )
    async def glob_files(
        pattern: Annotated[
            str, Field(description="Glob pattern, e.g. 'src/**/*.py' or '**/*test*.ts'.")
        ],
        path: Annotated[
            str, Field(description="Root directory to search from (default: '.').")
        ] = ".",
    ) -> str:
        ctx = current_execution_ctx.get()
        s_id = ctx.session_id if ctx else registry._session_id
        if not registry._driver or not s_id:
            return "Error: Sandbox not initialized for glob_files"
        await registry._ensure_sandbox(s_id)
        return await tier2.glob_files(
            registry._driver, s_id, pattern, registry.workspace_file(path)
        )

    @registry._tool(
        description=(
            "Search the codebase for symbols (functions, classes, types) matching a "
            "name or term. Prefer this for 'where is X defined' questions."
        )
    )
    async def code_search(
        query: Annotated[str, Field(description="Symbol name or search term.")],
        path: Annotated[str, Field(description="Directory to search from (default: '.').")] = ".",
        file_glob: Annotated[
            str | None, Field(description="Optional glob filter, e.g. '*.py'.")
        ] = None,
        max_results: Annotated[
            int, Field(description="Maximum results to return (default: 100).")
        ] = 100,
    ) -> str:
        ctx = current_execution_ctx.get()
        active_session = ctx.session_id if ctx else registry._session_id
        if registry._on_code_search_callback and active_session:
            try:
                semantic = await registry._on_code_search_callback(query, max_results)
                if semantic:
                    return semantic
            except Exception:
                # Semantic index unavailable for this session; fall back to symbols.
                pass
        target = registry._sandbox_target("code_search")
        if not target:
            return "Error: Sandbox not initialized for code_search"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        return await code_search_tools.search_code(
            driver, session_id, query, registry.workspace_file(path), file_glob, max_results
        )
