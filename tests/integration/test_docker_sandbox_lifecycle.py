"""Integration test for DockerSandboxDriver verifying container lifecycle and volume persistence."""

import uuid

import pytest
from sandbox_driver.docker_driver import DockerSandboxDriver

from specifications.interfaces.sandbox import (
    ResourceLimits,
    SandboxStatus,
    WorkspaceSpec,
)


@pytest.mark.asyncio
async def test_docker_sandbox_lifecycle_and_volume_persistence() -> None:
    session_id = f"test_{uuid.uuid4().hex[:8]}"
    driver = DockerSandboxDriver(default_image="python:3.12-slim")

    spec = WorkspaceSpec(
        session_id=session_id,
        tenant_org_id="org_test",
        tenant_user_id="user_test",
        container_image="python:3.12-slim",
        resources=ResourceLimits(cpu_cores=1.0, memory_limit="1Gi"),
    )

    try:
        # 1. Ensure workspace creates volume and starts in hibernated state
        await driver.ensure_workspace(spec)
        status = await driver.get_status(session_id)
        assert status == SandboxStatus.HIBERNATED

        # 2. Start sandbox container
        await driver.start_sandbox(session_id)
        status = await driver.get_status(session_id)
        assert status == SandboxStatus.RUNNING

        # 3. Command execution verification
        cmd_result = await driver.exec_command(
            session_id=session_id,
            command="python3 -c \"print('hello from sandbox')\"",
        )
        assert cmd_result.exit_code == 0
        assert cmd_result.stdout == "hello from sandbox\n"
        assert cmd_result.duration_ms > 0

        # 4. Write data to volume via exec and write_file API
        exec_write = await driver.exec_command(
            session_id=session_id,
            command='echo "rocket-v1" > /workspace/session_data.txt',
        )
        assert exec_write.exit_code == 0

        await driver.write_file(
            session_id=session_id,
            path="config.py",
            content="API_VERSION = 1\nDEBUG = True\n",
        )

        read_config = await driver.read_file(session_id=session_id, path="config.py")
        assert read_config == "API_VERSION = 1\nDEBUG = True\n"

        # Sliced read verification
        line1 = await driver.read_file(
            session_id=session_id, path="config.py", start_line=1, end_line=1
        )
        assert line1 == "API_VERSION = 1\n"

        # 5. Hibernate container (compute = 0, container destroyed, volume retained)
        await driver.hibernate_sandbox(session_id)
        status = await driver.get_status(session_id)
        assert status == SandboxStatus.HIBERNATED

        # 6. Resume sandbox container and verify volume persistence
        await driver.start_sandbox(session_id)
        status = await driver.get_status(session_id)
        assert status == SandboxStatus.RUNNING

        persisted_content = await driver.read_file(session_id=session_id, path="session_data.txt")
        assert persisted_content.strip() == "rocket-v1"

        persisted_config = await driver.read_file(session_id=session_id, path="config.py")
        assert persisted_config == "API_VERSION = 1\nDEBUG = True\n"

        # 7. Apply patch test
        patch = (
            "--- config.py\n"
            "+++ config.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-API_VERSION = 1\n"
            "+API_VERSION = 2\n"
            " DEBUG = True\n"
        )
        patch_applied = await driver.apply_patch(
            session_id=session_id,
            path="config.py",
            patch_content=patch,
        )
        assert patch_applied is True

        patched_config = await driver.read_file(session_id=session_id, path="config.py")
        assert "API_VERSION = 2\n" in patched_config

        # 8. Directory tree listing
        file_tree = await driver.list_files(session_id=session_id, directory="/workspace")
        file_paths = [f.path for f in file_tree]
        assert any("session_data.txt" in p for p in file_paths)
        assert any("config.py" in p for p in file_paths)

    finally:
        # 9. Clean up workspace and volume
        await driver.destroy_workspace(session_id)
        final_status = await driver.get_status(session_id)
        assert final_status == SandboxStatus.DESTROYED
