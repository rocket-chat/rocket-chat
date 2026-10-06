"""
Unit tests for multi-session concurrency safety.
Verifies that simultaneous turns on the same orchestrator/registry instance
never cross-contaminate event queues or callbacks.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry

from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.llm import ModelRequest, StreamChunk


@pytest.mark.asyncio
async def test_concurrent_sessions_isolated_event_queues() -> None:
    """Two sessions executing concurrently should receive only their own events."""
    mock_gateway = MagicMock()
    registry = ToolRegistry()

    async def simulated_stream(request: ModelRequest) -> AsyncIterator[StreamChunk]:
        content = request.messages[-1].content or ""
        # If it's a subagent delegated task prompt, return summary text
        if "Delegated Task:" in content:
            yield StreamChunk(text_delta="Delegated task completed.", is_finished=True)
            return

        if "session-alpha" in content:
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "tc-alpha",
                    "function": {
                        "name": "delegate_subagent",
                        "arguments": json.dumps({"role": "researcher", "task": "Alpha research"}),
                    },
                }
            )
            yield StreamChunk(text_delta="Alpha response", is_finished=True)
        elif "session-beta" in content:
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "tc-beta",
                    "function": {
                        "name": "delegate_subagent",
                        "arguments": json.dumps(
                            {"role": "qa_verifier", "task": "Beta verification"}
                        ),
                    },
                }
            )
            yield StreamChunk(text_delta="Beta response", is_finished=True)
        else:
            yield StreamChunk(text_delta="Turn completed.", is_finished=True)

    mock_gateway.chat_stream = simulated_stream

    orchestrator = AsyncReActOrchestrator(
        gateway=mock_gateway,
        registry=registry,
    )

    alpha_events: list[AgentEvent] = []
    beta_events: list[AgentEvent] = []

    async def run_alpha() -> None:
        async for ev in orchestrator.process_user_turn(
            "session-alpha", "Please run session-alpha tasks."
        ):
            alpha_events.append(ev)

    async def run_beta() -> None:
        async for ev in orchestrator.process_user_turn(
            "session-beta", "Please run session-beta tasks."
        ):
            beta_events.append(ev)

    # Run simultaneously
    await asyncio.gather(run_alpha(), run_beta())

    # Assert no event bleeding
    assert all(ev.session_id == "session-alpha" for ev in alpha_events)
    assert all(ev.session_id == "session-beta" for ev in beta_events)

    alpha_sub_events = [e for e in alpha_events if e.event_type == "subagent_started"]
    beta_sub_events = [e for e in beta_events if e.event_type == "subagent_started"]

    assert len(alpha_sub_events) == 1
    assert alpha_sub_events[0].payload["role"] == "researcher"

    assert len(beta_sub_events) == 1
    assert beta_sub_events[0].payload["role"] == "qa_verifier"
