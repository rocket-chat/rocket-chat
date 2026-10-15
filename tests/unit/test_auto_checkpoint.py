"""The workspace is snapshotted once, before a turn first changes it."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult, WorkspaceSpec


class _GitDriver(FakeSandboxDriver):
    """A fake whose workspace answers git commands the way a repository does."""

    def __init__(self, *, is_repo: bool = True, fail_snapshot: bool = False) -> None:
        super().__init__()
        self.is_repo = is_repo
        self.fail_snapshot = fail_snapshot
        self.snapshots = 0

    async def exec_command(
        self, session_id: str, command: str, *args: Any, **kwargs: Any
    ) -> ExecResult:
        if command.startswith("git rev-parse --git-dir"):
            self.exec_calls.append((session_id, command))
            return ExecResult(0 if self.is_repo else 128, ".git\n", "", 1)
        if command.startswith("git rev-parse --show-toplevel"):
            self.exec_calls.append((session_id, command))
            return ExecResult(0, "/workspace\n" if self.is_repo else "", "", 1)
        if command.startswith("git tag -l"):
            self.exec_calls.append((session_id, command))
            return ExecResult(0, "0\n", "", 1)
        if "commit-tree" in command:
            self.exec_calls.append((session_id, command))
            if self.fail_snapshot:
                return ExecResult(1, "", "fatal: boom", 1)
            self.snapshots += 1
            return ExecResult(0, "abc123\n", "", 1)
        return await super().exec_command(session_id, command, *args, **kwargs)


@pytest.fixture
def turn() -> Iterator[None]:
    token = current_execution_ctx.set(
        ExecutionContext(session_id="s1", event_queue=asyncio.Queue(), parent_model="m")
    )
    yield
    current_execution_ctx.reset(token)


async def _registry(driver: _GitDriver) -> ToolRegistry:
    await driver.ensure_workspace(
        WorkspaceSpec(session_id="s1", tenant_org_id="o", tenant_user_id="u", container_image="i")
    )
    await driver.start_sandbox("s1")
    return ToolRegistry(driver=driver, session_id="s1")


async def test_one_snapshot_precedes_the_first_change_and_not_later_ones(turn: None) -> None:
    driver = _GitDriver()
    registry = await _registry(driver)

    await registry.call_tool("file_write", {"path": "a.txt", "content": "1"})
    await registry.call_tool("file_write", {"path": "b.txt", "content": "2"})
    await registry.call_tool("bash_exec", {"command": "echo hi"})

    assert driver.snapshots == 1
    first_snapshot = next(i for i, (_, c) in enumerate(driver.exec_calls) if "commit-tree" in c)
    assert all("a.txt" not in c for _, c in driver.exec_calls[:first_snapshot])


async def test_reading_never_snapshots(turn: None) -> None:
    driver = _GitDriver()
    registry = await _registry(driver)
    await registry.call_tool("list_dir", {"path": "."})
    await registry.call_tool("file_read", {"path": "README.md"})
    assert driver.snapshots == 0


async def test_a_directory_that_is_not_a_repository_is_left_alone(turn: None) -> None:
    driver = _GitDriver(is_repo=False)
    registry = await _registry(driver)
    result = await registry.call_tool("file_write", {"path": "a.txt", "content": "1"})
    assert driver.snapshots == 0
    assert not result.startswith("Error")


async def test_a_failing_snapshot_never_blocks_the_work(turn: None) -> None:
    driver = _GitDriver(fail_snapshot=True)
    registry = await _registry(driver)
    result = await registry.call_tool("file_write", {"path": "a.txt", "content": "1"})
    assert "Error: fatal: boom" not in result
    assert "a.txt" in driver._files["s1"]


async def test_each_turn_gets_its_own_snapshot() -> None:
    driver = _GitDriver()
    registry = await _registry(driver)
    for _ in range(2):
        token = current_execution_ctx.set(
            ExecutionContext(session_id="s1", event_queue=asyncio.Queue(), parent_model="m")
        )
        try:
            await registry.call_tool("file_write", {"path": "a.txt", "content": "1"})
        finally:
            current_execution_ctx.reset(token)
    assert driver.snapshots == 2
