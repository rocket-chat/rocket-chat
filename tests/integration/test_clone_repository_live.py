"""The clone tool against a real container and a real public repository (skipped offline)."""

from __future__ import annotations

import shutil
import socket
import subprocess
import uuid

import pytest
from agent_core.tools.repository import clone_repository
from sandbox_driver.docker_driver import DockerSandboxDriver

from specifications.interfaces.sandbox import WorkspaceSpec

IMAGE = "rocket-chat-sandbox:dev"


def _ready() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False)
    if probe.returncode != 0:
        return False
    try:
        socket.create_connection(("github.com", 443), timeout=3).close()
    except OSError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _ready(), reason="needs the devbox image and network access")


async def test_the_devbox_clones_a_public_repository_as_an_unprivileged_user() -> None:
    driver = DockerSandboxDriver()
    session_id = f"clone-{uuid.uuid4().hex[:6]}"
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

        first = await clone_repository(driver, session_id, "octocat/Hello-World")
        again = await clone_repository(driver, session_id, "octocat/Hello-World")
        missing = await clone_repository(driver, session_id, "octocat/does-not-exist-" + session_id)
        listing = await driver.exec_command(session_id, "ls /workspace/Hello-World")

        assert first.startswith("Cloned octocat/Hello-World into /workspace/Hello-World")
        assert "README" in first
        assert again.startswith("Already cloned")
        assert "GitHub refused access" in missing
        assert "README" in listing.stdout
    finally:
        await driver.destroy_workspace(session_id)
