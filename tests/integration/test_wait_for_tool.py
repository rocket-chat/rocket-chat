"""Integration tests for durable waits and webhook resumption (Item F1)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.tools.registry import ToolRegistry

from specifications.interfaces.agent import TurnCallbacks, WaitOutcome
from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent


@pytest.mark.asyncio
async def test_wait_for_tool_emits_wait_started_event() -> None:
    session_id = "s_wait_1"
    event_bus = MagicMock(spec=EventBusProtocol)
    event_bus.publish = AsyncMock()

    events_published: list[SessionEvent] = []

    async def mock_publish(event: SessionEvent) -> None:
        events_published.append(event)

    event_bus.publish.side_effect = mock_publish

    # Mock github watch callback
    mock_watch = AsyncMock(return_value="Now following rocket-chat/platform#42.")

    async def mock_wait_for(
        target: str, identifier: str, timeout_seconds: int | None, sha: str | None
    ) -> WaitOutcome:
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.WAIT_STARTED,
                payload={
                    "wait_id": "wait_test_123",
                    "wait_type": target,
                    "target": identifier,
                    "timeout_seconds": timeout_seconds,
                },
            )
        )
        if target in ("ci_status", "review"):
            parts = identifier.split("#")
            await mock_watch(parts[0], int(parts[1]), branch=None, wake=True)
            return WaitOutcome(f"Waiting for {target} on {identifier}.", registered=True)
        return WaitOutcome(f"Waiting for {target} ({identifier}).", registered=True)

    callbacks = TurnCallbacks(wait_for=mock_wait_for)
    exec_ctx = ExecutionContext(
        session_id=session_id,
        event_queue=asyncio.Queue(),
        parent_model="test-model",
        tenant_org_id="org_1",
        callbacks=callbacks,
    )

    token = current_execution_ctx.set(exec_ctx)
    try:
        registry = ToolRegistry(session_id=session_id)
        result = await registry.call_tool(
            "wait_for",
            {
                "target": "ci_status",
                "identifier": "rocket-chat/platform#42",
                "timeout_seconds": 600,
            },
        )
        assert "Waiting for ci_status on rocket-chat/platform#42" in result
        assert mock_watch.await_count == 1
        assert exec_ctx.wait_registered, "the step will end the turn"

        # Check WAIT_STARTED event published
        wait_events = [e for e in events_published if e.event_type == EventType.WAIT_STARTED]
        assert len(wait_events) == 1
        assert wait_events[0].payload["wait_type"] == "ci_status"
        assert wait_events[0].payload["target"] == "rocket-chat/platform#42"
    finally:
        current_execution_ctx.reset(token)


@pytest.mark.asyncio
async def test_wait_for_time_delay() -> None:
    session_id = "s_wait_time"
    event_bus = MagicMock(spec=EventBusProtocol)
    events_published: list[SessionEvent] = []

    async def mock_publish(event: SessionEvent) -> None:
        events_published.append(event)

    event_bus.publish = AsyncMock(side_effect=mock_publish)

    async def mock_wait_for(
        target: str, identifier: str, timeout_seconds: int | None, sha: str | None
    ) -> WaitOutcome:
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.WAIT_STARTED,
                payload={
                    "wait_id": "wait_time_123",
                    "wait_type": target,
                    "target": identifier,
                    "timeout_seconds": timeout_seconds,
                },
            )
        )
        return WaitOutcome(f"Waiting for timer delay of {identifier}s.", registered=True)

    callbacks = TurnCallbacks(wait_for=mock_wait_for)
    exec_ctx = ExecutionContext(
        session_id=session_id,
        event_queue=asyncio.Queue(),
        parent_model="test-model",
        tenant_org_id="org_1",
        callbacks=callbacks,
    )

    token = current_execution_ctx.set(exec_ctx)
    try:
        registry = ToolRegistry(session_id=session_id)
        result = await registry.call_tool(
            "wait_for",
            {"target": "time", "identifier": "30", "timeout_seconds": 60},
        )
        assert "Waiting for timer delay of 30s" in result
        assert len(events_published) == 1
        assert events_published[0].payload["wait_type"] == "time"
    finally:
        current_execution_ctx.reset(token)
