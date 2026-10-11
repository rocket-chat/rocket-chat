"""Running commands: shell, tests, lint, type checks, builds, packages, a browser and image viewing."""

import logging
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from agent_core.context import current_execution_ctx
from agent_core.tools import browser as browser_tools
from agent_core.tools import images as image_tools
from agent_core.tools import package_manager as package_manager_tools
from agent_core.tools import tier2, verification

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register(registry: "ToolRegistry") -> None:
    _register_shell_tools(registry)
    _register_inspection_tools(registry)


def _register_shell_tools(registry: "ToolRegistry") -> None:
    @registry._tool(description="Execute a shell command inside the active sandbox environment.")
    async def bash_exec(
        command: Annotated[str, Field(description="The shell command to execute.")],
        timeout_seconds: Annotated[
            int, Field(description="Execution timeout in seconds (default: 120).")
        ] = 120,
    ) -> str:
        ctx = current_execution_ctx.get()
        s_id = ctx.session_id if ctx else registry._session_id
        if not registry._driver or not s_id:
            return "Error: Sandbox not initialized for bash_exec"
        await registry._ensure_sandbox(s_id)
        return await tier2.bash_exec(registry._driver, s_id, command, timeout_seconds)

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
    ) -> str:
        ctx = current_execution_ctx.get()
        s_id = ctx.session_id if ctx else registry._session_id
        if not registry._driver or not s_id:
            return "Error: Sandbox not initialized for run_tests"
        await registry._ensure_sandbox(s_id)
        return await tier2.run_tests(registry._driver, s_id, command, timeout_seconds)

    @registry._tool(
        description="Run the project linter (auto-detects npm/ruff/clippy/go vet/make)."
    )
    async def lint(
        command: Annotated[
            str | None,
            Field(description="Optional explicit lint command overriding detection."),
        ] = None,
        timeout_seconds: Annotated[int, Field(description="Timeout (default 600).")] = 600,
    ) -> str:
        target = registry._sandbox_target("lint")
        if not target:
            return "Error: Sandbox not initialized for lint"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        return await verification.run_check(driver, session_id, "lint", command, timeout_seconds)

    @registry._tool(
        description="Run the project type checker (auto-detects npm/mypy/tsc/cargo/go)."
    )
    async def typecheck(
        command: Annotated[
            str | None,
            Field(description="Optional explicit typecheck command overriding detection."),
        ] = None,
        timeout_seconds: Annotated[int, Field(description="Timeout (default 600).")] = 600,
    ) -> str:
        target = registry._sandbox_target("typecheck")
        if not target:
            return "Error: Sandbox not initialized for typecheck"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        return await verification.run_check(
            driver, session_id, "typecheck", command, timeout_seconds
        )

    @registry._tool(description="Build the project (auto-detects npm/python/cargo/go/make).")
    async def build(
        command: Annotated[
            str | None,
            Field(description="Optional explicit build command overriding detection."),
        ] = None,
        timeout_seconds: Annotated[int, Field(description="Timeout (default 600).")] = 600,
    ) -> str:
        target = registry._sandbox_target("build")
        if not target:
            return "Error: Sandbox not initialized for build"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        return await verification.run_check(driver, session_id, "build", command, timeout_seconds)

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
            Field(description="Package names for add/remove, applied in the given order."),
        ] = None,
        manager: Annotated[
            Literal["uv", "pnpm", "npm", "poetry"] | None,
            Field(description="Optional explicit manager overriding workspace detection."),
        ] = None,
        dev: Annotated[
            bool, Field(description="Add as a development dependency (add only).")
        ] = False,
        timeout_seconds: Annotated[int, Field(description="Timeout (default 600).")] = 600,
    ) -> str:
        target = registry._sandbox_target("package_manager")
        if not target:
            return "Error: Sandbox not initialized for package_manager"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        return await package_manager_tools.package_manager(
            driver, session_id, action, packages, manager, dev, timeout_seconds
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
