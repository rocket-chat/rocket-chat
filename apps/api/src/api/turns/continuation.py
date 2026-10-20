"""Automatic turn continuation when long-running session goals remain incomplete."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from agent_core.checklist import OPEN_STATUSES, unfinished_notice

from api.services.goals import InMemoryGoalStore, PostgresGoalStore
from api.services.settings import effective_settings
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.session import GoalStatus, GoalStoreProtocol, SessionGoal

if TYPE_CHECKING:
    from api.turns.context import TurnContext

logger = logging.getLogger(__name__)

_in_memory_goals = InMemoryGoalStore()


def get_goal_store(db_mgr: Any) -> GoalStoreProtocol:
    return PostgresGoalStore(db_mgr) if db_mgr is not None else _in_memory_goals


def has_unfinished_work(ctx: TurnContext) -> bool:
    """Whether the session still has pending or in-progress checklist or plan tasks."""
    meta = ctx.session.metadata if isinstance(ctx.session.metadata, dict) else {}
    plan_md = meta.get("plan_md") or ""
    if "- [ ]" in plan_md or "[ ]" in plan_md:
        return True
    checklist = meta.get("checklist")
    if isinstance(checklist, list):
        if any(isinstance(t, dict) and t.get("status") in OPEN_STATUSES for t in checklist):
            return True
    return unfinished_notice(ctx.session.conversation_history) is not None


async def check_and_schedule_continuation(ctx: TurnContext) -> bool:
    """If turn budget exceeded with unfinished work, schedule the next turn within safety budgets."""
    if not ctx.state.had_post_failure_synthesis:
        return False
    if not has_unfinished_work(ctx):
        goal_store = get_goal_store(ctx.req.db_mgr)
        goal = await goal_store.get_goal(ctx.session_id, ctx.session.tenant_org_id)
        if goal is not None and goal.status == GoalStatus.ACTIVE:
            goal.status = GoalStatus.COMPLETED
            await goal_store.update_goal(goal)
            await _publish_goal_update(ctx, goal)
        return False

    goal_store = get_goal_store(ctx.req.db_mgr)
    goal = await goal_store.get_goal(ctx.session_id, ctx.session.tenant_org_id)
    if goal is None:
        budgets = await effective_settings(ctx.req.db_mgr, ctx.session.tenant_org_id, "budgets")
        sess_cap = float(budgets.get("session_budget_usd", 0.0) or 0.0)
        max_cost = sess_cap if sess_cap > 0 else None
        max_tokens = int(budgets["goal_max_total_tokens"]) or None
        max_wall_time = int(budgets["goal_max_wall_time_minutes"]) * 60 or None
        max_continuations = int(budgets["max_continuations"])
        if max_continuations <= 0:
            return False
        goal = SessionGoal(
            id=f"goal_{uuid.uuid4().hex[:16]}",
            session_id=ctx.session_id,
            tenant_org_id=ctx.session.tenant_org_id,
            description=ctx.session.title or "Complete session tasks",
            status=GoalStatus.ACTIVE,
            max_continuations=max_continuations,
            max_cost_usd=max_cost,
            max_total_tokens=max_tokens,
            max_wall_time_seconds=max_wall_time,
        )
        await goal_store.create_goal(goal)

    trace = ctx.state.trace
    tokens = (trace.prompt_tokens + trace.completion_tokens) if trace else 0
    cost = trace.cost_usd if trace else 0.0
    goal, exceeded, warning = await goal_store.record_consumption(
        ctx.session_id, ctx.session.tenant_org_id, tokens, cost
    )
    if goal is None:
        return False
    if exceeded:
        logger.warning(
            "Goal budget exceeded for %s (%s); stopping automatic continuation",
            ctx.session_id,
            warning,
        )
        await _publish_goal_update(ctx, goal)
        return False

    goal.continuation_count += 1
    await goal_store.update_goal(goal)
    ctx.state.is_continuation = True

    await ctx.req.event_bus.publish(
        SessionEvent(
            session_id=ctx.session_id,
            event_type=EventType.CONTINUATION_SCHEDULED,
            payload={
                "continuation_index": goal.continuation_count,
                "max_continuations": goal.max_continuations,
                "reason": "Turn step limit reached with unfinished tasks in PLAN.md",
                "goal_id": goal.id,
            },
        )
    )
    from api.turns.tasks import _spawn_background_turn

    _spawn_background_turn(
        session_id=ctx.session_id,
        prompt="Continue execution of unfinished tasks from .rocket/PLAN.md and .rocket/PROGRESS.md.",
        orchestrator=ctx.req.orchestrator,
        event_bus=ctx.req.event_bus,
        driver=ctx.req.driver,
        registry=ctx.req.registry,
        store=ctx.req.store,
        model=ctx.effective_model,
        git_engine=ctx.req.git_engine,
        db_mgr=ctx.req.db_mgr,
        concurrency_gate=ctx.req.concurrency_gate,
        session_locks=ctx.req.session_locks,
        synthetic_prompt=True,
        overlay=ctx.req.overlay,
    )
    return True


async def _publish_goal_update(ctx: TurnContext, goal: SessionGoal | None) -> None:
    if goal is None:
        return
    await ctx.req.event_bus.publish(
        SessionEvent(
            session_id=ctx.session_id,
            event_type=EventType.GOAL_UPDATED,
            payload={
                "goal_id": goal.id,
                "description": goal.description,
                "status": goal.status.value,
                "consumed_tokens": goal.consumed_tokens,
                "consumed_cost_usd": goal.consumed_cost_usd,
                "continuation_count": goal.continuation_count,
                "max_continuations": goal.max_continuations,
                "stop_condition": goal.stop_condition,
            },
        )
    )
