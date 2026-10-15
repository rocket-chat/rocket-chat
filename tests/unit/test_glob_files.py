"""``glob_files`` lists a repository the way its own tools do, and says when it stopped early."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest
from agent_core.tools import tier2

from tests.support.local_git_driver import LocalGitDriver


def _tree(root: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("build/\n.venv/\nnode_modules/\n")
    for relative in (
        "src/app.py",
        "src/pkg/util.py",
        "tests/test_app.py",
        ".venv/lib/site.py",
        "node_modules/x/index.py",
        "build/gen.py",
        ".git/hooks/fake.py",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n")


def _driver(tmp: pathlib.Path, tools: bool) -> LocalGitDriver:
    workspace = tmp / "ws"
    workspace.mkdir()
    _tree(workspace)
    driver = LocalGitDriver(workspace, tmp / "shim")
    if not tools:
        # Only the system tools: the `find` fallback, as in a minimal image.
        driver._path = f"{tmp / 'shim'}:/usr/bin:/bin"
    return driver


@pytest.mark.parametrize(
    "tools",
    [
        pytest.param(
            True, marks=pytest.mark.skipif(not shutil.which("rg"), reason="needs ripgrep")
        ),
        False,
    ],
)
async def test_dependency_build_and_git_folders_are_not_listed(
    tmp_path: pathlib.Path, tools: bool
) -> None:
    driver = _driver(tmp_path, tools)

    out = await tier2.glob_files(driver, "s", "**/*.py")  # type: ignore[arg-type]

    assert out.splitlines() == ["src/app.py", "src/pkg/util.py", "tests/test_app.py"]


async def test_a_capped_listing_says_how_many_there_were(tmp_path: pathlib.Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "many").mkdir(parents=True)
    for n in range(tier2.GLOB_LIMIT + 20):
        (workspace / "many" / f"f{n}.txt").write_text("")
    driver = LocalGitDriver(workspace, tmp_path / "shim")
    driver._path = f"{tmp_path / 'shim'}:{os.environ['PATH']}"

    out = await tier2.glob_files(driver, "s", "many/*.txt")  # type: ignore[arg-type]

    assert (
        out.splitlines()[-1]
        == f"[500 of {tier2.GLOB_LIMIT + 20} matches shown; narrow the pattern or the path]"
    )


@pytest.mark.skipif(
    not shutil.which("docker")
    or subprocess.run(
        ["docker", "image", "inspect", "rocket-chat-sandbox:dev"], capture_output=True
    ).returncode
    != 0,
    reason="needs Docker and rocket-chat-sandbox:dev (which has ripgrep)",
)
async def test_in_the_sandbox_image_ripgrep_honours_gitignore() -> None:
    import uuid

    from sandbox_driver.docker_driver import DockerSandboxDriver

    from specifications.interfaces.sandbox import WorkspaceSpec

    driver = DockerSandboxDriver(default_image="rocket-chat-sandbox:dev")
    session = f"glob-{uuid.uuid4().hex[:8]}"
    await driver.ensure_workspace(WorkspaceSpec(session, "o", "u", "rocket-chat-sandbox:dev"))
    try:
        await driver.start_sandbox(session)
        await driver.exec_command(
            session,
            "git init -q && printf 'build/\\n.venv/\\nnode_modules/\\n' > .gitignore && "
            "mkdir -p src/pkg tests .venv/lib node_modules/x build && "
            "touch src/app.py src/pkg/util.py tests/test_app.py .venv/lib/site.py "
            "node_modules/x/index.py build/gen.py && command -v rg",
        )
        out = await tier2.glob_files(driver, session, "**/*.py")
    finally:
        await driver.destroy_workspace(session)

    assert out.splitlines() == ["src/app.py", "src/pkg/util.py", "tests/test_app.py"]
