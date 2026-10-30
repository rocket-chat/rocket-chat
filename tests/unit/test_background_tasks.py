"""Background processes have one set of tools; the removed ``task_*`` aliases map onto them."""

from __future__ import annotations

import pytest
from agent_core.tool_loading import RENAMED_TOOLS, current_tool_names
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver


def test_the_duplicate_task_aliases_are_gone() -> None:
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="test_sess")
    tool_names = {t.name for t in registry.get_tool_definitions()}
    assert not tool_names & set(RENAMED_TOOLS)
    assert set(RENAMED_TOOLS.values()) <= tool_names


def test_an_agent_list_with_an_old_alias_keeps_the_tool() -> None:
    assert current_tool_names(["task_start", "process_start", "file_read"]) == [
        "process_start",
        "file_read",
    ]


@pytest.mark.asyncio
async def test_process_list_returns_no_processes_when_sandbox_not_running() -> None:
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="test_sess")
    res = await registry.call_tool("process_list", {})
    assert "No background processes" in res
