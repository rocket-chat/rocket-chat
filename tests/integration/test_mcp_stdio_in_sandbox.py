"""A real stdio MCP server inside a real Docker sandbox; skipped without the image."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import uuid

import pytest
from api.services.sandbox_stdio import sandbox_stdio_transport
from mcp import Client
from sandbox_driver.docker_driver import DockerSandboxDriver

from specifications.interfaces.sandbox import WorkspaceSpec

IMAGE = "rocket-chat-sandbox:dev"


def _image_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False)
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(not _image_available(), reason=f"Docker image {IMAGE} not built")

SERVER = pathlib.Path(__file__).parents[1] / "support" / "minimal_mcp_server.py"


async def test_tools_are_listed_called_and_the_server_stops_with_the_transport() -> None:
    driver = DockerSandboxDriver()
    session_id = f"mcp-{uuid.uuid4().hex[:6]}"
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
        await driver.write_file(session_id, "/workspace/server.py", SERVER.read_text())
        transport = sandbox_stdio_transport(
            driver, session_id, "python3 /workspace/server.py", {"MY_TOKEN": "s3cret"}
        )
        async with Client(transport) as client:
            assert [t.name for t in (await client.list_tools()).tools] == ["echo"]
            big = await client.call_tool("echo", {"text": "x" * 20_000})
            assert len(big.content[0].text) == len("echo: ") + 20_000  # type: ignore[union-attr]

        left = await driver.exec_command(
            session_id, "pgrep -f '[p]ython3 /workspace/server.py' | xargs -r ps -o stat= -p"
        )
        assert left.stdout.replace("Z", "").strip() == "", "no live server process remains"
    finally:
        await driver.destroy_workspace(session_id)
