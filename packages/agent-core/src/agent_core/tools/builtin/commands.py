"""Running commands: shell, tests, lint, type checks, builds, packages, a browser and image viewing."""

import logging
import posixpath
import re
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field
from sandbox_driver.workspace import RepositoryNotFoundError, workspace_path

from agent_core.context import current_execution_ctx
from agent_core.tools import browser as browser_tools
from agent_core.tools import git_tools, tier2, verification, workspace_changes
from agent_core.tools import images as image_tools
from agent_core.tools import package_manager as package_manager_tools
from agent_core.tools.builtin.files import emit_file_diff
from specifications.interfaces.sandbox import SandboxDriverProtocol

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register(registry: "ToolRegistry") -> None:
    _register_shell_tools(registry)
    _register_verification_tools(registry)
    _register_inspection_tools(registry)


ProjectPath = Annotated[
    str | None,
    Field(
        description="Project folder, e.g. 'my-app' or 'my-app/packages/api'. Default: the "
        "session's active repository, or the only repository under /workspace."
    ),
]


async def _project(
    registry: "ToolRegistry", tool: str, path: str | None
) -> tuple[SandboxDriverProtocol, str, str] | str:
    """``(driver, session_id, folder)`` a project command runs in, or the error text."""
    target = registry._sandbox_target(tool)
    if not target:
        return f"Error: Sandbox not initialized for {tool}"
    driver, session_id = target
    await registry._ensure_sandbox(session_id)
    try:
        return driver, session_id, await registry.project_dir(session_id, path)
    except RepositoryNotFoundError as err:
        return f"Error: {err}"


async def _maybe_auto_checkpoint(
    driver: SandboxDriverProtocol, session_id: str, command: str, workdir: str
) -> None:
    if op := git_tools.classify_risky_git_operation(command):
        try:
            await git_tools.git_checkpoint(driver, session_id, label=f"auto-{op}", workdir=workdir)
        except Exception:
            pass


# A plain folder after a leading `cd`: no variables, globs, quotes or other shell syntax.
_LEADING_CD = re.compile(r"^\s*cd\s+([A-Za-z0-9_./@+-]+)\s*&&\s*(?=\S)")


def fold_leading_cd(command: str, workdir: str) -> tuple[str, str]:
    """``cd <folder> && rest`` as ``rest`` run in that folder.

    Models keep writing the folder into every command although ``cwd`` says it; the command is
    the same, and the session reads as the work instead of as navigation.
    """
    match = _LEADING_CD.match(command)
    if match is None:
        return command, workdir
    folder = match.group(1)
    target = folder if folder.startswith("/") else posixpath.normpath(f"{workdir}/{folder}")
    return command[match.end() :], target


def _register_shell_tools(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Execute a shell command inside the sandbox (/bin/sh by default; write POSIX-compliant "
            "syntax or invoke bash explicitly if using bashisms). Each call is a fresh shell: "
            "`cd` and exported variables do not carry over. Always say where it runs with `cwd`; "
            "never start the command with `cd`."
        )
    )
    async def bash_exec(
        command: Annotated[str, Field(description="The shell command to execute, without `cd`.")],
        cwd: Annotated[
            str,
            Field(
                description="Folder to run in, e.g. /workspace/api (absolute, or relative to "
                "/workspace)."
            ),
        ],
        timeout_seconds: Annotated[
            int, Field(description="Execution timeout in seconds (default: 120).")
        ] = 120,
    ) -> str:
        target = registry._sandbox_target("bash_exec")
        if not target:
            return "Error: Sandbox not initialized for bash_exec"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        workdir = workspace_path(cwd) if cwd.strip() else await registry.shell_dir(session_id)
        command, workdir = fold_leading_cd(command, workdir)
        await _maybe_auto_checkpoint(driver, session_id, command, workdir)
        before = await workspace_changes.snapshot(driver, session_id, workdir)
        output = await tier2.bash_exec(driver, session_id, command, timeout_seconds, workdir)

        if notice := git_tools.notice_for_manual_github_write(command):
            output = f"{notice}\n\n{output}" if output else notice

        after = await workspace_changes.snapshot(driver, session_id, workdir)
        changed = workspace_changes.changed_paths(before, after)
        for diff in await workspace_changes.diffs_for(driver, session_id, after[0], changed):
            emit_file_diff(registry, diff)
        return output

    @registry._tool(
        description=(
            "Install or manage project dependencies through the workspace's declared "
            "package manager (uv, pnpm, npm or poetry). Lockfile-aware; returns a "
            "parsed failure cause when an install fails."
        )
    )
    async def package_manager(
        action: Annotated[
            Literal["install", "add", "remove"],
            Field(description="Operation to perform (default: install)."),
        ] = "install",
        packages: Annotated[
            list[str] | None,
            Field(description="Package names to add or remove (e.g. ['pytest', 'ruff'])."),
        ] = None,
        manager: Annotated[
            Literal["uv", "pnpm", "npm", "poetry"] | None,
            Field(description="Explicit package manager override (auto-detected if omitted)."),
        ] = None,
        dev: Annotated[
            bool, Field(description="Add as a development dependency (add only).")
        ] = False,
        timeout_seconds: Annotated[int, Field(description="Timeout (default 600).")] = 600,
        path: ProjectPath = None,
    ) -> str:
        resolved = await _project(registry, "package_manager", path)
        if isinstance(resolved, str):
            return resolved
        driver, session_id, folder = resolved
        return await package_manager_tools.package_manager(
            driver, session_id, action, packages, manager, dev, timeout_seconds, folder
        )


def _register_verification_tools(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Run the project test suite, auto-detecting npm/cargo/go/pytest, and return"
            " a parsed pass/fail summary. Use this to verify changes before finishing."
        )
    )
    async def run_tests(
        command: Annotated[
            str | None,
            Field(description="Optional explicit test command overriding detection."),
        ] = None,
        timeout_seconds: Annotated[
            int, Field(description="Test execution timeout in seconds (default: 600).")
        ] = 600,
        path: ProjectPath = None,
    ) -> str:
        resolved = await _project(registry, "run_tests", path)
        if isinstance(resolved, str):
            return resolved
        driver, session_id, folder = resolved
        return await tier2.run_tests(driver, session_id, command, timeout_seconds, folder)

    @registry._tool(
        description="Run the project linter (auto-detects npm/ruff/clippy/go vet/make)."
    )
    async def lint(
        command: Annotated[
            str | None,
            Field(description="Optional explicit lint command overriding detection."),
        ] = None,
        timeout_seconds: Annotated[int, Field(description="Timeout (default 600).")] = 600,
        path: ProjectPath = None,
    ) -> str:
        resolved = await _project(registry, "lint", path)
        if isinstance(resolved, str):
            return resolved
        driver, session_id, folder = resolved
        return await verification.run_check(
            driver, session_id, "lint", command, timeout_seconds, folder
        )

    @registry._tool(
        description="Run the project type checker (auto-detects npm/mypy/tsc/cargo/go)."
    )
    async def typecheck(
        command: Annotated[
            str | None,
            Field(description="Optional explicit typecheck command overriding detection."),
        ] = None,
        timeout_seconds: Annotated[int, Field(description="Timeout (default 600).")] = 600,
        path: ProjectPath = None,
    ) -> str:
        resolved = await _project(registry, "typecheck", path)
        if isinstance(resolved, str):
            return resolved
        driver, session_id, folder = resolved
        return await verification.run_check(
            driver, session_id, "typecheck", command, timeout_seconds, folder
        )

    @registry._tool(description="Build the project (auto-detects npm/python/cargo/go/make).")
    async def build(
        command: Annotated[
            str | None,
            Field(description="Optional explicit build command overriding detection."),
        ] = None,
        timeout_seconds: Annotated[int, Field(description="Timeout (default 600).")] = 600,
        path: ProjectPath = None,
    ) -> str:
        resolved = await _project(registry, "build", path)
        if isinstance(resolved, str):
            return resolved
        driver, session_id, folder = resolved
        return await verification.run_check(
            driver, session_id, "build", command, timeout_seconds, folder
        )


def _register_inspection_tools(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Open a page in a headless browser inside the sandbox and report what a person "
            "would see: title, page structure, visible text, console errors and failed "
            "requests. Use it to check an app you started (for example "
            "http://localhost:3000/) instead of guessing."
        )
    )
    async def browser_inspect(
        url: Annotated[str, Field(description="Full http(s) URL, e.g. http://localhost:3000/.")],
        screenshot: Annotated[
            bool, Field(description="Also save a screenshot to the workspace (default: false).")
        ] = False,
        wait_ms: Annotated[
            int, Field(description="Extra time to let the page settle, in ms (default: 500).")
        ] = 500,
    ) -> str:
        target = registry._sandbox_target("browser_inspect")
        if target is None:
            return "Error: Sandbox not initialized for browser_inspect"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        return await browser_tools.browser_inspect(driver, session_id, url, screenshot, wait_ms)

    @registry._tool(
        description=(
            "Look at an image file from the workspace (a screenshot, a diagram, a design). "
            "The image is shown to you with your next step; it needs a model that can see "
            "images, and says so if yours cannot. Formats: PNG, JPEG, GIF, WebP, up to 5 MB. "
            "Use it to check a UI you built or to read a screenshot instead of guessing."
        )
    )
    async def view_image(
        path: Annotated[str, Field(description="Image path inside /workspace.")],
    ) -> str:
        target = registry._sandbox_target("view_image")
        if target is None:
            return "Error: Sandbox not initialized for view_image"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        ctx = current_execution_ctx.get()
        return await image_tools.load_image(
            driver, session_id, path, ctx.turn_images if ctx is not None else None
        )
