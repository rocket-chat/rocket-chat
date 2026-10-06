"""Unit tests for DockerSandboxDriver and helper utilities using mocks."""

from unittest.mock import MagicMock

import docker.errors
import pytest
from sandbox_driver.docker_driver import DockerSandboxDriver
from sandbox_driver.patcher import apply_unified_diff
from sandbox_driver.utils import (
    create_tar_archive,
    extract_file_from_tar,
    normalize_workspace_path,
    parse_memory_to_bytes,
)

from specifications.interfaces.sandbox import (
    ResourceLimits,
    SandboxStatus,
    WorkspaceSpec,
)


def test_normalize_workspace_path() -> None:
    assert normalize_workspace_path("file.txt") == "/workspace/file.txt"
    assert normalize_workspace_path("/custom/file.txt") == "/custom/file.txt"
    assert normalize_workspace_path("sub/dir/file.txt") == "/workspace/sub/dir/file.txt"


def test_parse_memory_to_bytes() -> None:
    assert parse_memory_to_bytes("4Gi") == 4 * 1024 * 1024 * 1024
    assert parse_memory_to_bytes("512Mi") == 512 * 1024 * 1024
    assert parse_memory_to_bytes("2g") == 2 * 1024 * 1024 * 1024
    assert parse_memory_to_bytes("1024k") == 1024 * 1024
    assert parse_memory_to_bytes("invalid") == 4 * 1024 * 1024 * 1024


def test_tar_archive_roundtrip() -> None:
    data = b"print('rocket test')\n"
    archive = create_tar_archive("script.py", data)
    extracted = extract_file_from_tar(archive, "script.py")
    assert extracted == "print('rocket test')\n"


def test_apply_unified_diff_success() -> None:
    original = "line 1\nline 2\nline 3\n"
    patch = (
        "--- file.txt\n+++ file.txt\n@@ -1,3 +1,3 @@\n line 1\n-line 2\n+line 2 modified\n line 3\n"
    )
    success, result = apply_unified_diff(original, patch)
    assert success is True
    assert result == "line 1\nline 2 modified\nline 3\n"


def test_apply_unified_diff_mismatch() -> None:
    original = "different content\n"
    patch = "@@ -1,1 +1,1 @@\n-expected content\n+replacement\n"
    success, result = apply_unified_diff(original, patch)
    assert success is False
    assert result == original


@pytest.mark.asyncio
async def test_ensure_workspace_creates_volume_idempotently() -> None:
    mock_client = MagicMock()
    mock_client.volumes.get.side_effect = docker.errors.NotFound("Not found")

    driver = DockerSandboxDriver(client=mock_client)
    spec = WorkspaceSpec(
        session_id="test_session_1",
        tenant_org_id="org_1",
        tenant_user_id="user_1",
        container_image="python:3.12-slim",
        resources=ResourceLimits(cpu_cores=1.0, memory_limit="2Gi"),
    )

    await driver.ensure_workspace(spec)

    mock_client.volumes.get.assert_called_once_with("sandbox_vol_test_session_1")
    mock_client.volumes.create.assert_called_once_with(
        name="sandbox_vol_test_session_1",
        labels={
            "rocket.session.id": "test_session_1",
            "rocket.tenant.org": "org_1",
            "rocket.tenant.user": "user_1",
            "rocket.managed": "true",
        },
    )


@pytest.mark.asyncio
async def test_hibernate_sandbox_removes_container_only() -> None:
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_client.containers.get.return_value = mock_container

    driver = DockerSandboxDriver(client=mock_client)
    await driver.hibernate_sandbox("test_session_2")

    mock_client.containers.get.assert_called_once_with("sandbox_ctr_test_session_2")
    mock_container.remove.assert_called_once_with(force=True)
    # Named volume must not be touched during hibernate
    mock_client.volumes.get.assert_not_called()


@pytest.mark.asyncio
async def test_destroy_workspace_removes_container_and_volume() -> None:
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_volume = MagicMock()
    mock_client.containers.get.return_value = mock_container
    mock_client.volumes.get.return_value = mock_volume

    driver = DockerSandboxDriver(client=mock_client)
    await driver.destroy_workspace("test_session_3")

    mock_container.remove.assert_called_once_with(force=True)
    mock_volume.remove.assert_called_once_with(force=True)

    status = await driver.get_status("test_session_3")
    assert status == SandboxStatus.DESTROYED


@pytest.mark.asyncio
async def test_exec_command_timeout() -> None:
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.id = "mock_ctr_id"
    mock_client.containers.get.return_value = mock_container

    driver = DockerSandboxDriver(client=mock_client)
    # Simulate a hanging operation in thread
    import time

    driver._sync_stream_exec = MagicMock(side_effect=lambda *args, **kwargs: time.sleep(1))  # type: ignore[method-assign]

    from sandbox_driver.exceptions import SandboxTimeoutError

    with pytest.raises(SandboxTimeoutError):
        await driver.exec_command(
            session_id="test_timeout",
            command="sleep 10",
            timeout_seconds=0,
        )
