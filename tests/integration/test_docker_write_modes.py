"""Writing a file through the Docker driver keeps what the file was, and a new one is usable.

``put_archive`` stamps the mode and owner the archive names. Files used to come out root-owned
with mode 0644: an edited script lost its executable bit and the non-root agent could not change
a file it had just written.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid

import pytest
from sandbox_driver.docker_driver import DockerSandboxDriver

from specifications.interfaces.sandbox import WorkspaceSpec

IMAGE = "rocket-chat-sandbox:dev"
pytestmark = pytest.mark.skipif(
    not shutil.which("docker")
    or subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode != 0,
    reason=f"needs Docker and {IMAGE}",
)


async def test_an_edit_keeps_the_executable_bit_and_a_new_file_belongs_to_the_agent() -> None:
    driver = DockerSandboxDriver(default_image=IMAGE)
    session = f"modes-{uuid.uuid4().hex[:8]}"
    await driver.ensure_workspace(
        WorkspaceSpec(session, "o", "u", container_image=IMAGE, require_non_root=True)
    )
    try:
        await driver.start_sandbox(session)
        await driver.exec_command(
            session, "printf '#!/bin/sh\\necho v1\\n' > run.sh && chmod 755 run.sh"
        )

        await driver.write_file(session, "run.sh", "#!/bin/sh\necho v2\n")
        await driver.write_file(session, "nested/new.txt", "hello\n")
        checks = await driver.exec_command(
            session,
            "stat -c '%a %u' run.sh nested/new.txt && ./run.sh && echo more >> nested/new.txt "
            "&& id -u",
        )
    finally:
        await driver.destroy_workspace(session)

    lines = checks.stdout.split()
    assert checks.exit_code == 0, checks.stderr
    uid = lines[-1]
    assert lines[0:2] == ["755", uid], "the script keeps its mode and owner"
    assert lines[2:4] == ["644", uid], "a new file belongs to the sandbox user"
    assert "v2" in checks.stdout, "the edited script still runs"
