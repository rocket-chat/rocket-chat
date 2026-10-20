"""Unit tests for Two-Tier FastMCP ToolRegistry and individual tool implementations."""

from unittest.mock import AsyncMock

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
    import httpx
    from agent_core.tools import tier1

    async def fake_safe_get(url: str, **_: object) -> tuple[httpx.Response, bytes]:
        body = b'{"title": "Test Title", "version": 1}'
        response = httpx.Response(
            200,
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", url),
        )
        return response, body

    monkeypatch.setattr(tier1, "safe_get", fake_safe_get)

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
    bash_out = await registry.call_tool(
        "bash_exec", {"command": "echo hello world", "cwd": "/workspace"}
    )
    assert "hello world" in bash_out
    # Around the command itself, the repository's changed files are fingerprinted (diffs).
    mock_driver.exec_command.assert_any_await(
        session_id="test_session",
        command="echo hello world",
        workdir="/workspace",
        timeout_seconds=120,
    )

    # 2. file_read
    read_out = await registry.call_tool("file_read", {"path": "test.txt"})
    assert read_out == "     1\tfile content line 1\n"
    mock_driver.read_file.assert_awaited_once_with(
        session_id="test_session", path="/workspace/test.txt"
    )

    # 3. file_write
    write_out = await registry.call_tool(
        "file_write", {"path": "out.txt", "content": "hello", "overwrite": True}
    )
    assert "Successfully wrote" in write_out
    mock_driver.write_file.assert_awaited_once_with(
        session_id="test_session", path="/workspace/out.txt", content="hello", overwrite=True
    )

    # 4. apply_patch
    patch_out = await registry.call_tool(
        "apply_patch", {"path": "file.py", "patch_content": "diff content"}
    )
    assert "Patch successfully applied" in patch_out
    mock_driver.apply_patch.assert_awaited_once_with(
        session_id="test_session", path="/workspace/file.py", patch_content="diff content"
    )


@pytest.mark.asyncio
async def test_tier2_unbound_sandbox_error() -> None:
    registry = ToolRegistry()  # No sandbox bound
    result = await registry.call_tool("bash_exec", {"command": "ls", "cwd": "/workspace"})
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


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="update_status deprecated — model step text is now auto-derived as STATUS_CHANGED"
)
async def test_update_status_tool() -> None:
    import asyncio

    from agent_core.context import ExecutionContext, current_execution_ctx

    from specifications.interfaces.agent import AgentEvent

    event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
    ctx = ExecutionContext(
        session_id="test_sess_status",
        event_queue=event_queue,
        parent_model="test-model",
    )
    current_execution_ctx.set(ctx)

    registry = ToolRegistry(session_id="test_sess_status")
    res = await registry.call_tool("update_status", {"message": "Investigating auth middleware"})

    assert "Status updated: Investigating auth middleware" in res
    event = event_queue.get_nowait()
    assert event is not None
    assert event.event_type == "status"
    assert event.payload["message"] == "Investigating auth middleware"


@pytest.mark.asyncio
async def test_think_tool_emits_thought_event() -> None:
    import asyncio

    from agent_core.context import ExecutionContext, current_execution_ctx

    from specifications.interfaces.agent import AgentEvent

    event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
    ctx = ExecutionContext(
        session_id="test_sess_think",
        event_queue=event_queue,
        parent_model="test-model",
    )
    token = current_execution_ctx.set(ctx)
    try:
        registry = ToolRegistry(session_id="test_sess_think")
        res = await registry.call_tool("think", {"thought": "Check the cache prefix first"})
        assert "Reasoning checkpoint recorded" in res
        event = event_queue.get_nowait()
        assert event is not None
        assert event.event_type == "thought"
        assert event.payload["content"] == "Check the cache prefix first"
    finally:
        current_execution_ctx.reset(token)


def test_catalog_completion_tools_are_exposed() -> None:
    from agent_core.tools.registry import ToolRegistry

    registry = ToolRegistry()
    names = {definition.name for definition in registry.get_tool_definitions()}
    assert {"package_manager", "git_log", "apply_diff_file", "think"} <= names
    assert "think" in registry.PLAN_READ_ONLY_TOOLS
    assert "git_log" in registry.PLAN_READ_ONLY_TOOLS


_MULTI_FILE_DIFF = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-old a
+new a
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -1 +1 @@
-old b
+new b
"""


@pytest.mark.asyncio
async def test_apply_diff_file_denied_does_not_touch_workspace() -> None:
    mock_driver = AsyncMock(spec=SandboxDriverProtocol)
    registry = ToolRegistry(driver=mock_driver, session_id="s_diff")
    calls: list[str] = []

    async def _deny(action_type: str, description: str, risk_level: str) -> str:
        calls.append(action_type)
        return "Denied by user"

    registry.set_approval_callback(_deny)
    out = await registry.call_tool(
        "apply_diff_file", {"diff_content": _MULTI_FILE_DIFF, "path": "a.py"}
    )
    assert "not approved" in out.lower()
    assert calls == ["apply_diff_file"]
    mock_driver.apply_patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_diff_file_approved_applies_and_emits_diff() -> None:
    import asyncio

    from agent_core.context import ExecutionContext, current_execution_ctx

    from specifications.interfaces.agent import AgentEvent

    event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
    ctx = ExecutionContext(session_id="s_diff", event_queue=event_queue, parent_model="m")
    token = current_execution_ctx.set(ctx)
    try:
        mock_driver = AsyncMock(spec=SandboxDriverProtocol)
        mock_driver.read_file.side_effect = ["old a\n", "new a\n"]
        mock_driver.apply_patch.return_value = True
        registry = ToolRegistry(driver=mock_driver, session_id="s_diff")

        async def _approve(action_type: str, description: str, risk_level: str) -> str:
            return "Approved"

        registry.set_approval_callback(_approve)
        out = await registry.call_tool(
            "apply_diff_file", {"diff_content": _MULTI_FILE_DIFF, "path": "a.py"}
        )
        assert "Applied patch to 'a.py'" in out
        mock_driver.apply_patch.assert_awaited_once()
        assert mock_driver.apply_patch.call_args.kwargs["path"] == "a.py"
        assert "b.py" not in mock_driver.apply_patch.call_args.kwargs["patch_content"]
        event = event_queue.get_nowait()
        assert event is not None
        assert event.event_type == "file_diff"
        assert event.payload["path"] == "a.py"
    finally:
        current_execution_ctx.reset(token)


async def test_a_made_up_tool_is_refused_with_the_nearest_real_names() -> None:
    registry = ToolRegistry()

    reply = await registry.call_tool("file_reed", {"path": "x"})

    assert "no tool named 'file_reed'" in reply and "file_read" in reply
    assert "search_tools" in reply and "Do not call it again" in reply


async def test_a_made_up_tool_never_reaches_the_approval_gate() -> None:
    registry = ToolRegistry()
    registry._permission_refusal = AsyncMock(side_effect=AssertionError("asked a human"))  # type: ignore[method-assign]

    assert "no tool named" in await registry.call_tool("totally_invented", {})


async def test_search_tools_lists_and_filters_the_available_tools() -> None:
    registry = ToolRegistry()

    everything = await registry.call_tool("search_tools", {})
    filtered = await registry.call_tool("search_tools", {"query": "web"})
    nothing = await registry.call_tool("search_tools", {"query": "zzzz-nope"})

    assert "- file_read:" in everything and "- search_tools:" in everything
    assert "web_fetch" in filtered and "file_read" not in filtered
    assert "No tool matches" in nothing
