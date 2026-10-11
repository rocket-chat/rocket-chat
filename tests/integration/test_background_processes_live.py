"""Background processes in a real devbox container, as the unprivileged sandbox user."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from collections.abc import AsyncIterator

import pytest
from agent_core.tools import processes
from sandbox_driver.docker_driver import DockerSandboxDriver

from specifications.interfaces.sandbox import WorkspaceSpec

IMAGE = "rocket-chat-sandbox:dev"


def _ready() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False)
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(not _ready(), reason="needs Docker and the devbox image")


@pytest.fixture
async def sandbox() -> AsyncIterator[tuple[DockerSandboxDriver, str]]:
    driver = DockerSandboxDriver()
    session_id = f"proc-{uuid.uuid4().hex[:6]}"
    await driver.ensure_workspace(
        WorkspaceSpec(
            session_id=session_id,
            tenant_org_id="o",
            tenant_user_id="u",
            container_image=IMAGE,
            require_non_root=True,
        )
    )
    try:
        await driver.start_sandbox(session_id)
        yield driver, session_id
    finally:
        await driver.destroy_workspace(session_id)


def _id(message: str) -> str:
    return next(word for word in message.split() if processes.is_valid_id(word))


async def test_a_server_runs_in_the_background_and_answers_on_its_port(
    sandbox: tuple[DockerSandboxDriver, str],
) -> None:
    driver, session_id = sandbox
    started = await processes.start_process(
        driver, session_id, "python3 -m http.server 8123", name="static site", port=8123
    )
    process_id = _id(started)

    reachable = await driver.exec_command(
        session_id,
        "for i in 1 2 3 4 5 6 7 8 9 10; do curl -sf http://127.0.0.1:8123/ >/dev/null && echo up && exit 0; sleep 1; done; exit 1",
        timeout_seconds=30,
    )
    assert "up" in reachable.stdout

    listed = await processes.list_processes(driver, session_id)
    assert [(p.id, p.state, p.port) for p in listed] == [(process_id, "running", 8123)]
    assert "GET /" in await processes.read_output(driver, session_id, process_id)

    stopped = await processes.stop_process(driver, session_id, process_id)
    assert "stopped" in stopped
    gone = await driver.exec_command(
        session_id, "curl -sf -m 2 http://127.0.0.1:8123/ || echo down", timeout_seconds=15
    )
    assert "down" in gone.stdout, "stopping the process must free its port"


async def test_exit_codes_and_the_group_kill_work_in_the_container(
    sandbox: tuple[DockerSandboxDriver, str],
) -> None:
    driver, session_id = sandbox
    build = _id(
        await processes.start_process(driver, session_id, "echo compiling; sleep 1; exit 2")
    )
    result = await processes.wait_for_process(driver, session_id, build, timeout_seconds=30)
    assert "exited with code 2" in result and "compiling" in result

    tree = _id(await processes.start_process(driver, session_id, "(sleep 300 &) ; sleep 300"))
    await processes.stop_process(driver, session_id, tree)
    left = await driver.exec_command(session_id, "pgrep -x sleep || echo none", timeout_seconds=15)
    assert "none" in left.stdout, "the whole process group must die, not just the shell"
