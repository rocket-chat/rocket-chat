"""Every change to a file reaches the cockpit as one diff, whichever tool made it."""

from __future__ import annotations

import asyncio
import pathlib
import subprocess
from collections.abc import Iterator

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.tools.registry import ToolRegistry

from specifications.interfaces.agent import FileDiff, TurnCallbacks
from tests.support.local_git_driver import LocalGitDriver


@pytest.fixture
def diffs() -> Iterator[list[FileDiff]]:
    seen: list[FileDiff] = []
    ctx = ExecutionContext(
        session_id="s",
        event_queue=asyncio.Queue(),
        parent_model="m",
        callbacks=TurnCallbacks(diff=seen.append),
    )
    token = current_execution_ctx.set(ctx)
    yield seen
    current_execution_ctx.reset(token)


def _repo(tmp: pathlib.Path, folder: str = "app") -> tuple[LocalGitDriver, pathlib.Path]:
    workspace = tmp / "ws"
    repo = workspace / folder
    repo.mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    run("add", "-A")
    run("-c", "user.name=a", "-c", "user.email=a@b", "commit", "-q", "-m", "init")
    return LocalGitDriver(workspace, tmp / "shim"), repo


async def test_a_shell_edit_and_a_generated_file_are_reported(
    tmp_path: pathlib.Path, diffs: list[FileDiff]
) -> None:
    driver, _ = _repo(tmp_path)
    registry = ToolRegistry(driver=driver, session_id="s")  # type: ignore[arg-type]

    await registry.call_tool(
        "bash_exec",
        {
            "command": "sed -i.bak 's/a + b/b + a/' calc.py && rm calc.py.bak && echo x > gen.txt",
            "cwd": "app",
        },
    )

    by_path = {d.path: d for d in diffs}
    assert set(by_path) == {"app/calc.py", "app/gen.txt"}
    assert by_path["app/calc.py"].additions == 1 and by_path["app/calc.py"].deletions == 1
    assert by_path["app/gen.txt"].is_new_file


async def test_a_command_that_changes_nothing_reports_nothing(
    tmp_path: pathlib.Path, diffs: list[FileDiff]
) -> None:
    driver, repo = _repo(tmp_path)
    (repo / "calc.py").write_text("changed before\n")  # already modified: not this command's
    registry = ToolRegistry(driver=driver, session_id="s")  # type: ignore[arg-type]

    await registry.call_tool("bash_exec", {"command": "cat calc.py && ls", "cwd": "app"})

    assert diffs == []


async def test_file_tools_report_each_change_exactly_once(
    tmp_path: pathlib.Path, diffs: list[FileDiff]
) -> None:
    driver, _ = _repo(tmp_path)
    registry = ToolRegistry(driver=driver, session_id="s")  # type: ignore[arg-type]

    await registry.call_tool("file_write", {"path": "app/new.py", "content": "x = 1\n"})
    await registry.call_tool(
        "file_edit",
        {"path": "app/calc.py", "target_block": "a + b", "replacement_block": "a - b"},
    )
    patch = "--- a/new.py\n+++ b/new.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    await registry.call_tool("apply_patch", {"path": "app/new.py", "patch_content": patch})

    assert [(d.path, d.is_new_file) for d in diffs] == [
        ("app/new.py", True),
        ("app/calc.py", False),
        ("app/new.py", False),
    ]
