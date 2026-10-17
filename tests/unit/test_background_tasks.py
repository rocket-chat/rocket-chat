"""Tests for background task tools."""

from __future__ import annotations

import pytest
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver


@pytest.mark.asyncio
async def test_background_task_aliases_registered() -> None:
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id="test_sess")
    tool_names = {t.name for t in registry.get_tool_definitions()}

    assert "task_start" in tool_names
    assert "task_list" in tool_names
    assert "task_status" in tool_names
    assert "task_kill" in tool_names


@pytest.mark.asyncio
async def test_task_list_returns_no_processes_when_sandbox_not_running() -> None:
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id="test_sess")
    res = await registry.call_tool("task_list", {})
    assert "No background processes" in res
