"""``bash_exec`` reports everything a command printed: stderr on success, output before a timeout."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from typing import Any

import pytest
from agent_core.tools import tier2
from sandbox_driver.exceptions import SandboxTimeoutError

from specifications.interfaces.sandbox import ExecResult


class _Driver:
    def __init__(self, result: ExecResult | None = None, error: Exception | None = None) -> None:
        self.result, self.error = result, error

    async def exec_command(self, *args: Any, **kwargs: Any) -> ExecResult:
        if self.error:
            raise self.error
        assert self.result is not None
        return self.result


async def test_stderr_of_a_successful_command_is_kept() -> None:
    driver = _Driver(ExecResult(0, "built\n", "warning: deprecated flag\n", 1))

    out = await tier2.bash_exec(driver, "s", "make")  # type: ignore[arg-type]

    assert "STDOUT:\nbuilt" in out and "STDERR:\nwarning: deprecated flag" in out


async def test_a_quiet_success_and_a_failure_read_as_before() -> None:
    assert await tier2.bash_exec(_Driver(ExecResult(0, "", "", 1)), "s", "true") == (  # type: ignore[arg-type]
        "(Command completed with no output)"
    )
    failed = await tier2.bash_exec(_Driver(ExecResult(2, "", "boom\n", 1)), "s", "x")  # type: ignore[arg-type]
    assert failed.startswith("Command failed with exit code 2") and "STDERR:\nboom" in failed


async def test_a_timeout_reports_what_was_printed_before_it() -> None:
    error = SandboxTimeoutError("timed out", "step 1 ok\nstep 2 ok\n", "waiting for lock...\n")

    out = await tier2.bash_exec(_Driver(error=error), "s", "deploy", 30)  # type: ignore[arg-type]

    assert "timed out after 30s" in out and "process_start" in out
    assert "step 2 ok" in out and "waiting for lock" in out


@pytest.mark.skipif(
    not shutil.which("docker")
    or subprocess.run(
        ["docker", "image", "inspect", "rocket-chat-sandbox:dev"], capture_output=True
    ).returncode,
    reason="needs Docker and rocket-chat-sandbox:dev",
)
async def test_a_real_docker_timeout_keeps_the_partial_output() -> None:
    from sandbox_driver.docker_driver import DockerSandboxDriver

    from specifications.interfaces.sandbox import WorkspaceSpec

    driver = DockerSandboxDriver(default_image="rocket-chat-sandbox:dev")
    session = f"bash-out-{uuid.uuid4().hex[:8]}"
    await driver.ensure_workspace(
        WorkspaceSpec(session, "o", "u", container_image="rocket-chat-sandbox:dev")
    )
    try:
        await driver.start_sandbox(session)
        out = await tier2.bash_exec(
            driver, session, "echo before-the-hang; echo err-line >&2; sleep 30", timeout_seconds=2
        )
    finally:
        await driver.destroy_workspace(session)

    assert "timed out after 2s" in out
    assert "before-the-hang" in out and "err-line" in out
