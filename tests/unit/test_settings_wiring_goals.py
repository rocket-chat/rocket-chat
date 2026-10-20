"""Per-turn cost cap and the ceilings on a task's automatic continuation turns."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from api.services.settings import save_org_config
from api.turns.continuation import check_and_schedule_continuation, get_goal_store
from api.turns.shared import _turn_service

from specifications.interfaces.session import SessionRecord


async def _plan(org: str) -> Any:
    return await _turn_service.plan_turn(
        db_mgr=None,
        org_id=org,
        user_id="u1",
        agent_id=None,
        global_default_model="platform/default",
    )


@pytest.mark.setting("budgets.max_turn_cost_usd")
async def test_the_turn_cost_cap_is_carried_on_the_turn_policy() -> None:
    org = "wire_turn_cost"
    assert (await _plan(org)).policy.max_turn_cost_usd is None

    await save_org_config(None, org, "budgets", {"max_turn_cost_usd": 2.5}, [])
    assert (await _plan(org)).policy.max_turn_cost_usd == 2.5


def _unfinished_ctx(org: str, session_id: str) -> Any:
    bus = MagicMock()
    bus.publish = AsyncMock()
    return SimpleNamespace(
        session_id=session_id,
        effective_model="m",
        state=SimpleNamespace(
            had_post_failure_synthesis=True,
            is_continuation=False,
            trace=SimpleNamespace(prompt_tokens=10, completion_tokens=5, cost_usd=0.01),
        ),
        req=SimpleNamespace(
            db_mgr=None,
            event_bus=bus,
            orchestrator=None,
            driver=None,
            registry=None,
            store=None,
            git_engine=None,
            concurrency_gate=None,
            session_locks=None,
            overlay=None,
        ),
        session=SessionRecord(
            session_id=session_id,
            tenant_org_id=org,
            tenant_user_id="u1",
            title="Long task",
            metadata={"plan_md": "- [ ] still to do"},
        ),
    )


async def _goal(org: str, session_id: str) -> Any:
    return await get_goal_store(None).get_goal(session_id, org)


async def _continue(ctx: Any) -> bool:
    with patch("api.turns.tasks._spawn_background_turn") as spawn:
        scheduled = await check_and_schedule_continuation(ctx)
        assert spawn.called is scheduled
        return scheduled


@pytest.mark.setting("budgets.max_continuations")
async def test_the_continuation_limit_caps_how_often_a_task_resumes() -> None:
    org = "wire_continuations"
    await save_org_config(None, org, "budgets", {"max_continuations": 2}, [])
    ctx = _unfinished_ctx(org, "s_cont")

    assert await _continue(ctx) is True
    assert await _continue(ctx) is True
    assert await _continue(ctx) is False
    assert (await _goal(org, "s_cont")).max_continuations == 2


@pytest.mark.setting("budgets.max_continuations")
async def test_a_zero_continuation_limit_disables_continuation() -> None:
    org = "wire_no_continuations"
    await save_org_config(None, org, "budgets", {"max_continuations": 0}, [])

    assert await _continue(_unfinished_ctx(org, "s_none")) is False


@pytest.mark.setting("budgets.goal_max_total_tokens")
async def test_the_goal_token_ceiling_stops_continuation() -> None:
    org = "wire_goal_tokens"
    await save_org_config(None, org, "budgets", {"goal_max_total_tokens": 20}, [])
    ctx = _unfinished_ctx(org, "s_tokens")

    assert await _continue(ctx) is True  # 15 tokens used
    assert await _continue(ctx) is False  # 30 > 20
    assert (await _goal(org, "s_tokens")).max_total_tokens == 20


@pytest.mark.setting("budgets.goal_max_wall_time_minutes")
async def test_the_goal_wall_time_ceiling_is_set_from_minutes() -> None:
    org = "wire_goal_wall"
    await save_org_config(None, org, "budgets", {"goal_max_wall_time_minutes": 90}, [])

    await _continue(_unfinished_ctx(org, "s_wall"))
    assert (await _goal(org, "s_wall")).max_wall_time_seconds == 90 * 60
