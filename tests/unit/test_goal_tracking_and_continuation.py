"""Unit tests for automatic goal tracking, budgets and continuations (Item A3)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from api.services.goals import InMemoryGoalStore
from api.turns.continuation import has_unfinished_work
from api.turns.finalise import housekeeping, publish_followup

from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import GoalStatus, SessionGoal, SessionRecord


@pytest.mark.asyncio
async def test_goal_consumption_and_budget_warnings() -> None:
    store = InMemoryGoalStore()
    goal = SessionGoal(
        id="goal_1",
        session_id="sess_1",
        tenant_org_id="org_1",
        description="Migrate auth",
        max_total_tokens=100_000,
        max_cost_usd=10.0,
        max_continuations=5,
    )
    await store.create_goal(goal)

    # 1. Normal consumption under 80%
    updated, exceeded, warning = await store.record_consumption("sess_1", "org_1", 50_000, 4.0)
    assert not exceeded
    assert warning is None
    assert updated.consumed_tokens == 50_000
    assert updated.consumed_cost_usd == 4.0

    # 2. Consumption reaches 80% warning
    updated, exceeded, warning = await store.record_consumption("sess_1", "org_1", 35_000, 4.5)
    assert not exceeded
    assert warning is not None
    assert "80%" in warning

    # 3. Consumption exceeds budget
    updated, exceeded, warning = await store.record_consumption("sess_1", "org_1", 20_000, 2.0)
    assert exceeded
    assert updated.status == GoalStatus.FAILED
    assert "exceeded" in (warning or "")


def test_has_unfinished_work_detects_plan_and_checklist() -> None:
    # 1. Has unchecked task in plan_md
    ctx_plan = SimpleNamespace(
        session=SimpleNamespace(
            metadata={"plan_md": "# Plan\n- [x] Step 1\n- [ ] Step 2"},
            conversation_history=[],
        )
    )
    assert has_unfinished_work(ctx_plan)

    # 2. Completed plan
    ctx_done = SimpleNamespace(
        session=SimpleNamespace(
            metadata={"plan_md": "# Plan\n- [x] Step 1\n- [x] Step 2"},
            conversation_history=[],
        )
    )
    assert not has_unfinished_work(ctx_done)

    # 3. Open task in checklist
    ctx_checklist = SimpleNamespace(
        session=SimpleNamespace(
            metadata={},
            conversation_history=[
                ChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "update_task_checklist",
                                "arguments": '{"tasks": [{"id": "t1", "title": "Build", "status": "in_progress"}]}',
                            },
                        }
                    ],
                )
            ],
        )
    )
    assert has_unfinished_work(ctx_checklist)


@pytest.mark.asyncio
async def test_intermediate_continuation_skips_followup_and_housekeeping() -> None:
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    store = MagicMock()
    store.mutate_session = AsyncMock()
    store.get_session = AsyncMock(return_value=None)

    ctx = SimpleNamespace(
        session_id="s1",
        effective_model="test-model",
        req=SimpleNamespace(
            session_id="s1",
            synthetic_prompt=True,  # Synthetic prompt on continuation turn
            store=store,
            event_bus=event_bus,
            db_mgr=None,
            orchestrator=SimpleNamespace(gateway=MagicMock(), default_model="test-model"),
            prompt="Continue execution",
        ),
        session=SessionRecord(
            session_id="s1",
            tenant_org_id="o1",
            tenant_user_id="u1",
            title="Long Session",
        ),
        state=SimpleNamespace(
            is_continuation=True,
            turn_error=None,
            executed_tools=[],
            started_at=100.0,
            first_token_at=101.0,
            router=None,
            effective_model="test-model",
        ),
    )

    # 1. publish_followup should return early without publishing or mutating
    await publish_followup(ctx, "Partial step completion.")
    assert store.mutate_session.await_count == 0
    assert event_bus.publish.await_count == 0

    # 2. housekeeping should return early without session summary or AI titling
    await housekeeping(ctx.req, ctx.state)
    assert store.get_session.await_count == 0
