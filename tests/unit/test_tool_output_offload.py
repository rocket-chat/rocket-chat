"""Tests for offloading large tool results to workspace files (Item B4)."""

from __future__ import annotations

import pytest
from agent_core.tools.offload import offload_tool_output
from sandbox_driver.fake_driver import FakeSandboxDriver


@pytest.mark.asyncio
async def test_small_output_not_offloaded() -> None:
    driver = FakeSandboxDriver()
    output = "Short command output: line 1\nline 2\nline 3\n"
    res = await offload_tool_output(
        output, session_id="s1", call_id="c1", threshold=6000, driver=driver
    )
    assert res == output


@pytest.mark.asyncio
async def test_large_output_offloaded_to_file_with_head_and_tail_preserved() -> None:
    driver = FakeSandboxDriver()
    # Generate 200 kB / 500 lines output
    lines = [
        f"Line {i:04d}: data payload block with some details for testing output offload"
        for i in range(500)
    ]
    large_output = "\n".join(lines)
    assert len(large_output) > 20_000

    res = await offload_tool_output(
        large_output,
        session_id="s1",
        call_id="call_99",
        threshold=6000,
        driver=driver,
        turn_index=3,
    )

    # 1. Output length returned to model is significantly reduced
    assert len(res) < len(large_output)
    assert len(res) < 6000

    # 2. First 40 lines (head) are preserved
    assert "Line 0000:" in res
    assert "Line 0039:" in res

    # 3. Last 20 lines (tail) are preserved
    assert "Line 0480:" in res
    assert "Line 0499:" in res

    # 4. Notice containing file path is present
    expected_path = "/workspace/.rocket/outputs/3_call_99.txt"
    assert f"Full output saved to {expected_path}" in res

    # 5. Full file is durably written to sandbox and readable
    saved = await driver.read_file("s1", ".rocket/outputs/3_call_99.txt")
    assert saved == large_output

    # 6. Ranged read_file can page through middle lines
    middle_page = await driver.read_file(
        "s1", ".rocket/outputs/3_call_99.txt", start_line=100, end_line=110
    )
    assert "Line 0099:" in middle_page
    assert "Line 0109:" in middle_page


@pytest.mark.asyncio
async def test_tier1_mcp_output_offloaded_without_sandbox_and_read_back() -> None:
    from agent_core.tools.builtin.files import register
    from agent_core.tools.registry import ToolRegistry

    lines = [f"Item {i:03d}: mcp payload data line" for i in range(300)]
    large_mcp_output = "\n".join(lines)

    # 1. Offload with driver=None (e.g. Tier-1 MCP tool before sandbox boot)
    res = await offload_tool_output(
        large_mcp_output,
        session_id="s_mcp",
        call_id="call_jira",
        threshold=2000,
        driver=None,
        turn_index=1,
    )
    assert "Full output saved to /workspace/.rocket/outputs/1_call_jira.txt" in res

    # 2. Call file_read through ToolRegistry without driver
    registry = ToolRegistry(driver=None, session_id="s_mcp")
    register(registry)

    content = await registry.call_tool(
        "file_read",
        {"path": "/workspace/.rocket/outputs/1_call_jira.txt", "start_line": 50, "end_line": 60},
    )
    assert "[lines 50-60 of 300]" in content
    assert "Item 049:" in content
    assert "Item 059:" in content


@pytest.mark.asyncio
async def test_cached_offload_file_read_skips_ensure_sandbox() -> None:
    from unittest.mock import AsyncMock

    from agent_core.tools.builtin.files import register
    from agent_core.tools.registry import ToolRegistry

    lines = [f"Data line {i}" for i in range(100)]
    await offload_tool_output(
        "\n".join(lines),
        session_id="s_fast",
        call_id="call_fast",
        threshold=200,
        driver=None,
        turn_index=1,
    )
    registry = ToolRegistry(driver=AsyncMock(), session_id="s_fast")
    registry._ensure_sandbox = AsyncMock()
    register(registry)

    content = await registry.call_tool(
        "file_read",
        {"path": "/workspace/.rocket/outputs/1_call_fast.txt", "start_line": 1, "end_line": 5},
    )
    assert "Data line 0" in content
    registry._ensure_sandbox.assert_not_called()
