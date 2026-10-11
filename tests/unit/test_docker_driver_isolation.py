"""DockerSandboxDriver isolation flags and idle-tracking adoption (no daemon needed)."""

from __future__ import annotations

from unittest.mock import MagicMock

import docker
import pytest
from sandbox_driver.docker_driver import DockerSandboxDriver

from specifications.interfaces.sandbox import WorkspaceSpec


def _driver() -> tuple[DockerSandboxDriver, MagicMock]:
    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound("absent")
    client.volumes.get.side_effect = docker.errors.NotFound("absent")
    return DockerSandboxDriver(client=client), client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("non_root", "user", "cap_drop"),
    [
        (True, "1000:1000", ["ALL"]),
        (False, None, ["NET_RAW", "SYS_ADMIN", "SYS_PTRACE", "DAC_READ_SEARCH"]),
    ],
)
async def test_run_flags_follow_the_non_root_setting(
    non_root: bool, user: str | None, cap_drop: list[str]
) -> None:
    driver, client = _driver()
    await driver.ensure_workspace(
        WorkspaceSpec("s1", "org", "user", "img:1", require_non_root=non_root)
    )
    await driver.start_sandbox("s1")

    kwargs = client.containers.run.call_args.kwargs
    assert kwargs["user"] == user
    assert kwargs["cap_drop"] == cap_drop
    assert kwargs["security_opt"] == ["no-new-privileges:true"]


@pytest.mark.asyncio
async def test_containers_that_outlive_a_restart_are_adopted() -> None:
    driver, client = _driver()
    container = MagicMock()
    container.labels = {"rocket.session.id": "sess-old"}
    client.containers.list.return_value = [container]

    await driver.adopt_existing_sandboxes()

    assert "sess-old" in driver._last_activity
    assert client.containers.list.call_args.kwargs["filters"] == {"label": "rocket.managed=true"}


@pytest.mark.asyncio
async def test_hibernated_workspaces_that_outlive_a_restart_are_adopted_too() -> None:
    driver, client = _driver()
    client.containers.list.return_value = []
    volume = MagicMock()
    volume.attrs = {"Labels": {"rocket.session.id": "sess-hibernated"}}
    unlabelled = MagicMock()
    unlabelled.attrs = {"Labels": None}
    client.volumes.list.return_value = [volume, unlabelled]

    await driver.adopt_existing_sandboxes()

    assert set(driver._last_activity) == {"sess-hibernated"}
    assert client.volumes.list.call_args.kwargs["filters"] == {"label": "rocket.managed=true"}


@pytest.mark.asyncio
async def test_workspace_purge_is_disabled_unless_configured() -> None:
    driver, _ = _driver()
    driver._last_activity["s1"] = 0.0
    assert driver.inactivity_cleanup_seconds == 0
    assert await driver.cleanup_inactive_sandboxes() == []
