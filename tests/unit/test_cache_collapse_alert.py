import asyncio

import pytest
from agent_core.context import ExecutionContext
from agent_core.orchestrator import AsyncReActOrchestrator

from specifications.interfaces.agent import AgentEvent


@pytest.mark.asyncio
async def test_cache_collapse_alert_fires_on_three_consecutive_low_hit_calls() -> None:
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
    ctx = ExecutionContext(
        session_id="test_sess",
        event_queue=queue,
        parent_model="test-model",
    )

    # 1st call: low cache hit (< 20%)
    await AsyncReActOrchestrator._check_cache_collapse(ctx, 3000, 300, "test_sess", queue)
    assert ctx.consecutive_low_cache_calls == 1
    assert queue.empty()

    # 2nd call: low cache hit (< 20%)
    await AsyncReActOrchestrator._check_cache_collapse(ctx, 3000, 200, "test_sess", queue)
    assert ctx.consecutive_low_cache_calls == 2
    assert queue.empty()

    # 3rd call: low cache hit (< 20%) -> triggers alert
    await AsyncReActOrchestrator._check_cache_collapse(ctx, 3000, 100, "test_sess", queue)
    assert ctx.consecutive_low_cache_calls == 3
    assert not queue.empty()
    event = await queue.get()
    assert event is not None
    assert "Cache Collapse Detected" in event.payload["delta"]

    # 4th call: healthy cache hit (>= 20%) -> resets counter
    await AsyncReActOrchestrator._check_cache_collapse(ctx, 3000, 1500, "test_sess", queue)
    assert ctx.consecutive_low_cache_calls == 0
