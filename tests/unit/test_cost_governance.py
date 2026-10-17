"""Unit tests for cost governance and per-turn cost caps (Item J1)."""

from __future__ import annotations

from typing import Any

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry

from specifications.interfaces.agent import TurnPolicy
from specifications.interfaces.llm import LLMGatewayProtocol, ModelRequest, StreamChunk


class MockPriceyGateway(LLMGatewayProtocol):
    """Mock gateway that reports high token usage."""

    def __init__(self) -> None:
        self.call_count = 0

    async def chat_stream(self, request: ModelRequest):
        self.call_count += 1
        yield StreamChunk(text_delta="Working...")
        # 200,000 completion tokens at claude rates ($15/M) = $3.00 per call
        yield StreamChunk(
            is_finished=True,
            finish_reason="stop",
            token_usage={
                "prompt_tokens": 1000,
                "completion_tokens": 200_000,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            },
        )

    async def check_budget(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
    ) -> bool:
        return True

    async def record_usage(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        pass


@pytest.mark.asyncio
async def test_turn_cost_cap_stops_execution_at_step_boundary() -> None:
    gateway = MockPriceyGateway()
    registry = ToolRegistry()
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry, max_turns=10)

    # Policy with strict cost cap of $2.00 (step 1 costs ~$3.00)
    policy = TurnPolicy(max_turn_cost_usd=2.0)

    events: list[Any] = []
    async for ev in orchestrator.process_user_turn(
        session_id="s_cost",
        user_message="Execute costly multi-step analysis",
        policy=policy,
        model="claude-3-5-sonnet",
    ):
        events.append(ev)

    # Step 1 costs ~$3.00 (cumulative ~$3.00 >= $2.00) -> halted at step boundary
    assert gateway.call_count == 1
    # Verify status changed event mentions cost limit
    status_events = [e for e in events if e.event_type == "status"]
    assert any("Turn cost limit" in str(e.payload.get("message")) for e in status_events)
