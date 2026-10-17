"""Unit tests for the wait_for tool and durable waiting lifecycle (Item F1)."""

from __future__ import annotations

import pytest
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from tests.turn_harness import run_turn


@pytest.mark.asyncio
async def test_wait_for_tool_callback() -> None:
    session_id = "s_wait_tool"
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id=session_id)

    recorded_wait: list[tuple[str, str, int | None]] = []

    async def fake_wait_cb(target: str, identifier: str, timeout_seconds: int | None) -> str:
        recorded_wait.append((target, identifier, timeout_seconds))
        return f"Wrote wait {target}"

    registry._on_wait_for_callback = fake_wait_cb

    result = await registry.call_tool(
        "wait_for", {"target": "ci_status", "identifier": "org/repo#42", "timeout_seconds": 300}
    )
    assert result == "Wrote wait ci_status"
    assert len(recorded_wait) == 1
    assert recorded_wait[0] == ("ci_status", "org/repo#42", 300)


@pytest.mark.asyncio
async def test_orchestrator_terminates_turn_on_wait_for() -> None:
    outcome = await run_turn(
        "org_wait",
        script=[("wait_for", {"target": "time", "identifier": "5"})],
    )

    # Status should contain waiting event and final status should be waiting
    status_events = outcome.events_of("status_changed")
    waiting_statuses = [e for e in status_events if e.payload.get("status") == "waiting"]
    assert len(waiting_statuses) >= 1
    # Check that wait_started event was also emitted
    wait_started_events = outcome.events_of("wait_started")
    assert len(wait_started_events) >= 1
    assert wait_started_events[0].payload.get("wait_type") == "time"
