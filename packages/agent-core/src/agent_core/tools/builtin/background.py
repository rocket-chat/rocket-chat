"""Background processes: start, list, read, wait for and stop."""

import logging
from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from agent_core.context import current_execution_ctx
from agent_core.tools import processes as process_tools
from specifications.interfaces.sandbox import SandboxStatus

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


async def _do_process_start(
    registry: "ToolRegistry",
    command: str,
    name: str | None,
    port: int | None,
    workdir: str,
) -> str:
    target = registry._sandbox_target("process_start")
    if not target:
        return "Error: Sandbox not initialized for process_start"
    driver, session_id = target
    await registry._ensure_sandbox(session_id)
    ctx = current_execution_ctx.get()
    limit = ctx.callbacks.max_background_processes if ctx is not None else 5
    result = await process_tools.start_process(
        driver,
        session_id,
        command,
        name=name,
        port=port,
        workdir=workdir,
        max_running=limit,
    )
    await registry._processes_changed(session_id)
    return result


async def _do_process_list(registry: "ToolRegistry") -> str:
    target = registry._sandbox_target("process_list")
    if not target:
        return "Error: Sandbox not initialized for process_list"
    driver, session_id = target
    if await driver.get_status(session_id) != SandboxStatus.RUNNING:
        return "No background processes."
    found = await process_tools.list_processes(driver, session_id)
    await registry._processes_changed(session_id)
    return "\n".join(p.summary() for p in found) or "No background processes."


async def _do_process_output(registry: "ToolRegistry", process_id: str, lines: int) -> str:
    target = registry._sandbox_target("process_output")
    if not target:
        return "Error: Sandbox not initialized for process_output"
    driver, session_id = target
    return await process_tools.read_output(driver, session_id, process_id, lines)


async def _do_process_stop(registry: "ToolRegistry", process_id: str) -> str:
    target = registry._sandbox_target("process_stop")
    if not target:
        return "Error: Sandbox not initialized for process_stop"
    driver, session_id = target
    result = await process_tools.stop_process(driver, session_id, process_id)
    await registry._processes_changed(session_id)
    return result


def register(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Start a long-running command in the BACKGROUND (a dev server, a watcher, a "
            "slow build) and return at once with a process id. `bash_exec` blocks until "
            "its command ends and kills it on timeout, so use this for anything that keeps "
            "running. Declare `port` for a server: the person can then open it in the "
            "Preview tab. Read output with `process_output`, wait for it to finish with "
            "`process_wait`, stop it with `process_stop`. Processes end when the sandbox "
            "is hibernated; the session is told about any that exited while you were away."
        )
    )
    async def process_start(
        command: Annotated[str, Field(description="The shell command to run in the background.")],
        name: Annotated[
            str | None,
            Field(description="Short label shown to the person (default: the command)."),
        ] = None,
        port: Annotated[
            int | None, Field(description="TCP port the process serves on, if any.")
        ] = None,
        workdir: Annotated[
            str, Field(description="Directory to run in (default: /workspace).")
        ] = "/workspace",
    ) -> str:
        return await _do_process_start(registry, command, name, port, workdir)

    @registry._tool(description="List the background processes of this session and their state.")
    async def process_list() -> str:
        return await _do_process_list(registry)

    @registry._tool(
        description=("Read the latest output (stdout and stderr together) of a background process.")
    )
    async def process_output(
        process_id: Annotated[str, Field(description="Id returned by process_start.")],
        lines: Annotated[int, Field(description="How many trailing lines (max 500).")] = 100,
    ) -> str:
        return await _do_process_output(registry, process_id, lines)

    @registry._tool(
        description=(
            "Wait until a background process ends (or the timeout passes) and return its "
            "exit code and last output. Use it for builds and test runs started with "
            "process_start."
        )
    )
    async def process_wait(
        process_id: Annotated[str, Field(description="Id returned by process_start.")],
        timeout_seconds: Annotated[
            int, Field(description="How long to wait, at most 300 (default 60).")
        ] = 60,
    ) -> str:
        target = registry._sandbox_target("process_wait")
        if not target:
            return "Error: Sandbox not initialized for process_wait"
        driver, session_id = target
        result = await process_tools.wait_for_process(
            driver, session_id, process_id, timeout_seconds
        )
        await registry._processes_changed(session_id)
        return result

    @registry._tool(description="Stop a background process and everything it started.")
    async def process_stop(
        process_id: Annotated[str, Field(description="Id returned by process_start.")],
    ) -> str:
        return await _do_process_stop(registry, process_id)

    @registry._tool(
        description="Alias for process_start: launch a command as a detached background task."
    )
    async def task_start(
        command: Annotated[str, Field(description="The shell command to run in the background.")],
        name: Annotated[
            str | None,
            Field(description="Short label shown to the user (default: the command)."),
        ] = None,
        workdir: Annotated[
            str, Field(description="Directory to run in (default: /workspace).")
        ] = "/workspace",
    ) -> str:
        return await _do_process_start(registry, command, name, None, workdir)

    @registry._tool(description="Alias for process_list: list active background tasks.")
    async def task_list() -> str:
        return await _do_process_list(registry)

    @registry._tool(
        description="Alias for process_output: read output or check status of a background task."
    )
    async def task_status(
        task_id: Annotated[
            str, Field(description="Task id returned by task_start or process_start.")
        ],
        lines: Annotated[
            int, Field(description="How many trailing lines of output to read.")
        ] = 100,
    ) -> str:
        return await _do_process_output(registry, task_id, lines)

    @registry._tool(
        description="Alias for process_stop: cancel or terminate a running background task."
    )
    async def task_kill(
        task_id: Annotated[
            str, Field(description="Task id returned by task_start or process_start.")
        ],
    ) -> str:
        return await _do_process_stop(registry, task_id)
