"""Unit tests for Two-Tier FastMCP ToolRegistry and individual tool implementations."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.tier1 import web_fetch

from specifications.interfaces.sandbox import ExecResult, SandboxDriverProtocol


def test_tool_registry_definitions() -> None:
    registry = ToolRegistry()
    definitions = registry.get_tool_definitions()

    tool_names = [d.name for d in definitions]
    assert "web_fetch" in tool_names
    assert "web_search" in tool_names
    assert "bash_exec" in tool_names
    assert "file_read" in tool_names
    assert "file_write" in tool_names
    assert "apply_patch" in tool_names
    assert "session_rename" in tool_names


@pytest.mark.asyncio
async def test_tier1_web_fetch_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_response = MagicMock()
    mock_response.headers = {"content-type": "application/json"}
    mock_response.text = '{"title": "Test Title", "version": 1}'
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.get.return_value = mock_response

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: mock_client)

    result = await web_fetch("https://api.example.com/data.json")
    assert "Test Title" in result
    assert '"version": 1' in result


@pytest.mark.asyncio
async def test_tier2_sandbox_delegation() -> None:
    mock_driver = AsyncMock(spec=SandboxDriverProtocol)
    mock_driver.exec_command.return_value = ExecResult(
        exit_code=0, stdout="hello world\n", stderr="", duration_ms=50
    )
    mock_driver.read_file.return_value = "file content line 1\n"
    mock_driver.write_file.return_value = None
    mock_driver.apply_patch.return_value = True

    registry = ToolRegistry(driver=mock_driver, session_id="test_session")

    # 1. bash_exec
    bash_out = await registry.call_tool("bash_exec", {"command": "echo hello world"})
    assert "hello world" in bash_out
    mock_driver.exec_command.assert_awaited_once_with(
        session_id="test_session", command="echo hello world", timeout_seconds=120
    )

    # 2. file_read
    read_out = await registry.call_tool("file_read", {"path": "test.txt"})
    assert read_out == "file content line 1\n"
    mock_driver.read_file.assert_awaited_once_with(
        session_id="test_session", path="test.txt", start_line=None, end_line=None
    )

    # 3. file_write
    write_out = await registry.call_tool(
        "file_write", {"path": "out.txt", "content": "hello", "overwrite": True}
    )
    assert "Successfully wrote" in write_out
    mock_driver.write_file.assert_awaited_once_with(
        session_id="test_session", path="out.txt", content="hello", overwrite=True
    )

    # 4. apply_patch
    patch_out = await registry.call_tool(
        "apply_patch", {"path": "file.py", "patch_content": "diff content"}
    )
    assert "Patch successfully applied" in patch_out
    mock_driver.apply_patch.assert_awaited_once_with(
        session_id="test_session", path="file.py", patch_content="diff content"
    )


@pytest.mark.asyncio
async def test_tier2_unbound_sandbox_error() -> None:
    registry = ToolRegistry()  # No sandbox bound
    result = await registry.call_tool("bash_exec", {"command": "ls"})
    assert "Error: Sandbox not initialized" in result


@pytest.mark.asyncio
async def test_session_rename_callback() -> None:
    registry = ToolRegistry(session_id="test_session_123")
    renamed: list[tuple[str, str]] = []

    async def _on_rename(s_id: str, new_title: str) -> None:
        renamed.append((s_id, new_title))

    registry.set_rename_callback(_on_rename)
    res = await registry.call_tool("session_rename", {"title": "Refactor Navigation Cockpit"})

    assert "Refactor Navigation Cockpit" in res
    assert renamed == [("test_session_123", "Refactor Navigation Cockpit")]
