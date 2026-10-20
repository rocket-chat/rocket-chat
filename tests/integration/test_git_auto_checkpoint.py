"""Tests for git auto-checkpoint creation on risky operations (Item H3)."""

from __future__ import annotations

import pathlib

import pytest
from agent_core.tools.git_tools import (
    CHECKPOINT_TAG_PREFIX,
    classify_risky_git_operation,
)
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from tests.support.local_git_driver import LocalGitDriver


def test_classify_risky_git_operation() -> None:
    # Risky operations
    assert classify_risky_git_operation("git rebase origin/main") == "rebase"
    assert classify_risky_git_operation("git rebase -i HEAD~3") == "rebase"
    assert classify_risky_git_operation("git reset --hard HEAD~1") == "reset_hard"
    assert classify_risky_git_operation("git merge feature/branch") == "merge"
    assert classify_risky_git_operation("git cherry-pick 1234567") == "cherry_pick"
    assert classify_risky_git_operation("git filter-branch --env-filter ...") == "filter_branch"
    assert classify_risky_git_operation("git push origin main --force") == "force_push"
    assert classify_risky_git_operation("git push origin main --force-with-lease") == "force_push"
    assert classify_risky_git_operation("git push -f origin main") == "force_push"

    # Benign / ordinary operations
    assert classify_risky_git_operation("git status") is None
    assert classify_risky_git_operation("git diff") is None
    assert classify_risky_git_operation("git log -n 5") is None
    assert classify_risky_git_operation("git commit -m 'feat: update'") is None
    assert classify_risky_git_operation("git checkout -b new-branch") is None
    assert classify_risky_git_operation("git branch -a") is None
    assert classify_risky_git_operation("pytest tests/unit") is None


@pytest.mark.asyncio
async def test_auto_checkpoint_created_before_rebase() -> None:
    driver = FakeSandboxDriver()
    session_id = "s_auto_cp"
    registry = ToolRegistry(driver=driver, session_id=session_id)

    # Execute a risky rebase command via bash_exec
    output = await registry.call_tool(
        "bash_exec",
        {"command": "git rebase origin/main", "cwd": "/workspace"},
    )
    assert isinstance(output, str)

    # Verify that git_checkpoint was automatically called by checking driver exec calls
    exec_commands = [cmd for s_id, cmd in driver.exec_calls if s_id == session_id]
    auto_checkpoint_calls = [
        cmd for cmd in exec_commands if CHECKPOINT_TAG_PREFIX in cmd and "auto-rebase" in cmd
    ]
    assert len(auto_checkpoint_calls) >= 1


@pytest.mark.asyncio
async def test_auto_checkpoint_in_real_git_repository(tmp_path: pathlib.Path) -> None:
    import functools
    import subprocess

    import anyio

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    run_cmd = functools.partial(subprocess.run, cwd=repo_dir, check=True)
    await anyio.to_thread.run_sync(run_cmd, ["git", "init", "-b", "main"])
    await anyio.to_thread.run_sync(run_cmd, ["git", "config", "user.name", "Test Agent"])
    await anyio.to_thread.run_sync(run_cmd, ["git", "config", "user.email", "agent@test.local"])

    test_file = repo_dir / "README.md"
    test_file.write_text("# Test Repo\n")
    await anyio.to_thread.run_sync(run_cmd, ["git", "add", "."])
    await anyio.to_thread.run_sync(run_cmd, ["git", "commit", "-m", "Initial commit"])

    driver = LocalGitDriver(workdir=repo_dir, shim_dir=tmp_path / "shims")
    session_id = "s_real_cp"
    registry = ToolRegistry(driver=driver, session_id=session_id)

    # Risky operation: git rebase main
    output = await registry.call_tool(
        "bash_exec", {"command": "git rebase main", "cwd": "/workspace"}
    )
    assert isinstance(output, str)

    # Verify that a real git checkpoint tag was minted in the git repository
    tag_proc = await anyio.to_thread.run_sync(
        functools.partial(
            subprocess.run,
            ["git", "tag", "-l"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
    )
    tags = tag_proc.stdout.splitlines()
    matching_tags = [t for t in tags if t.startswith(CHECKPOINT_TAG_PREFIX) and "auto-rebase" in t]
    assert len(matching_tags) >= 1
