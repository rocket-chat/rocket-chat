"""Durable session goals and safety budgets storage."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import select

from api.db.models import SessionGoalModel
from specifications.interfaces.session import GoalStatus, GoalStoreProtocol, SessionGoal


def _to_goal(row: SessionGoalModel) -> SessionGoal:
    return SessionGoal(
        id=row.id,
        session_id=row.session_id,
        tenant_org_id=row.org_id,
        description=row.description,
        status=GoalStatus(row.status),
        max_wall_time_seconds=row.max_wall_time_seconds,
        max_total_tokens=row.max_total_tokens,
        max_cost_usd=row.max_cost_usd,
        consumed_tokens=row.consumed_tokens,
        consumed_cost_usd=row.consumed_cost_usd,
        continuation_count=row.continuation_count,
        max_continuations=row.max_continuations,
        stop_condition=row.stop_condition,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


class InMemoryGoalStore(GoalStoreProtocol):
    """In-memory store for session goals during tests and development."""

    def __init__(self) -> None:
        self._goals: dict[tuple[str, str], SessionGoal] = {}

    async def create_goal(self, goal: SessionGoal) -> None:
        key = (goal.tenant_org_id, goal.session_id)
        self._goals[key] = goal

    async def get_goal(self, session_id: str, tenant_org_id: str) -> SessionGoal | None:
        return self._goals.get((tenant_org_id, session_id))

    async def update_goal(self, goal: SessionGoal) -> None:
        key = (goal.tenant_org_id, goal.session_id)
        goal.updated_at = int(time.time())
        self._goals[key] = goal

    async def record_consumption(
        self,
        session_id: str,
        tenant_org_id: str,
        tokens: int,
        cost_usd: float,
    ) -> tuple[SessionGoal | None, bool, str | None]:
        goal = await self.get_goal(session_id, tenant_org_id)
        if goal is None:
            return None, True, "No goal found"
        if goal.status != GoalStatus.ACTIVE:
            return goal, True, f"Goal is not active ({goal.status.value})"
        goal.consumed_tokens += tokens
        goal.consumed_cost_usd += cost_usd
        goal.updated_at = int(time.time())
        exceeded, warning = self._check_budgets(goal)
        if exceeded:
            goal.status = GoalStatus.FAILED
        return goal, exceeded, warning

    def _check_budgets(self, goal: SessionGoal) -> tuple[bool, str | None]:
        if goal.max_wall_time_seconds and goal.created_at:
            elapsed = time.time() - goal.created_at
            if elapsed >= goal.max_wall_time_seconds:
                return (
                    True,
                    f"Wall time limit exceeded ({int(elapsed)}s/{goal.max_wall_time_seconds}s)",
                )
        if goal.max_total_tokens and goal.consumed_tokens >= goal.max_total_tokens:
            return True, f"Token budget exceeded ({goal.consumed_tokens}/{goal.max_total_tokens})"
        if goal.max_cost_usd and goal.consumed_cost_usd >= goal.max_cost_usd:
            return (
                True,
                f"Cost budget exceeded (${goal.consumed_cost_usd:.2f}/${goal.max_cost_usd:.2f})",
            )
        if goal.max_continuations and goal.continuation_count >= goal.max_continuations:
            return (
                True,
                f"Max continuations reached ({goal.continuation_count}/{goal.max_continuations})",
            )
        warning: str | None = None
        if goal.max_wall_time_seconds and goal.created_at:
            elapsed = time.time() - goal.created_at
            if elapsed >= 0.8 * goal.max_wall_time_seconds:
                warning = f"Wall time budget at 80% ({int(elapsed)}s/{goal.max_wall_time_seconds}s)"
        if (
            not warning
            and goal.max_total_tokens
            and goal.consumed_tokens >= 0.8 * goal.max_total_tokens
        ):
            warning = f"Token budget at 80% ({goal.consumed_tokens}/{goal.max_total_tokens})"
        elif (
            not warning and goal.max_cost_usd and goal.consumed_cost_usd >= 0.8 * goal.max_cost_usd
        ):
            warning = f"Cost budget at 80% (${goal.consumed_cost_usd:.2f}/${goal.max_cost_usd:.2f})"
        return False, warning


class PostgresGoalStore(GoalStoreProtocol):
    """PostgreSQL RLS-scoped storage for session goals."""

    def __init__(self, db_manager: Any) -> None:
        self._db = db_manager

    async def create_goal(self, goal: SessionGoal) -> None:
        now = int(time.time())
        row = SessionGoalModel(
            id=goal.id or f"goal_{uuid.uuid4().hex[:24]}",
            session_id=goal.session_id,
            org_id=goal.tenant_org_id,
            description=goal.description,
            status=goal.status.value,
            max_wall_time_seconds=goal.max_wall_time_seconds,
            max_total_tokens=goal.max_total_tokens,
            max_cost_usd=goal.max_cost_usd,
            consumed_tokens=goal.consumed_tokens,
            consumed_cost_usd=goal.consumed_cost_usd,
            continuation_count=goal.continuation_count,
            max_continuations=goal.max_continuations,
            stop_condition=goal.stop_condition,
            created_at=goal.created_at or now,
            updated_at=goal.updated_at or now,
            completed_at=goal.completed_at,
        )
        async with self._db.session(tenant_org_id=goal.tenant_org_id) as session:
            session.add(row)
            await session.commit()

    async def get_goal(self, session_id: str, tenant_org_id: str) -> SessionGoal | None:
        async with self._db.session(tenant_org_id=tenant_org_id) as session:
            stmt = select(SessionGoalModel).where(
                SessionGoalModel.session_id == session_id,
                SessionGoalModel.org_id == tenant_org_id,
            )
            row = (await session.scalars(stmt)).first()
            return _to_goal(row) if row else None

    async def update_goal(self, goal: SessionGoal) -> None:
        async with self._db.session(tenant_org_id=goal.tenant_org_id) as session:
            stmt = select(SessionGoalModel).where(
                SessionGoalModel.session_id == goal.session_id,
                SessionGoalModel.org_id == goal.tenant_org_id,
            )
            row = (await session.scalars(stmt)).first()
            if row is None:
                return
            row.status = goal.status.value
            row.consumed_tokens = goal.consumed_tokens
            row.consumed_cost_usd = goal.consumed_cost_usd
            row.continuation_count = goal.continuation_count
            row.stop_condition = goal.stop_condition
            row.updated_at = int(time.time())
            row.completed_at = goal.completed_at
            await session.commit()

    async def record_consumption(
        self,
        session_id: str,
        tenant_org_id: str,
        tokens: int,
        cost_usd: float,
    ) -> tuple[SessionGoal | None, bool, str | None]:
        goal = await self.get_goal(session_id, tenant_org_id)
        if goal is None:
            return None, True, "No goal found"
        if goal.status != GoalStatus.ACTIVE:
            return goal, True, f"Goal is not active ({goal.status.value})"
        goal.consumed_tokens += tokens
        goal.consumed_cost_usd += cost_usd
        exceeded, warning = self._check_budgets(goal)
        if exceeded:
            goal.status = GoalStatus.FAILED
        await self.update_goal(goal)
        return goal, exceeded, warning

    def _check_budgets(self, goal: SessionGoal) -> tuple[bool, str | None]:
        if goal.max_wall_time_seconds and goal.created_at:
            elapsed = time.time() - goal.created_at
            if elapsed >= goal.max_wall_time_seconds:
                return (
                    True,
                    f"Wall time limit exceeded ({int(elapsed)}s/{goal.max_wall_time_seconds}s)",
                )
        if goal.max_total_tokens and goal.consumed_tokens >= goal.max_total_tokens:
            return True, f"Token budget exceeded ({goal.consumed_tokens}/{goal.max_total_tokens})"
        if goal.max_cost_usd and goal.consumed_cost_usd >= goal.max_cost_usd:
            return (
                True,
                f"Cost budget exceeded (${goal.consumed_cost_usd:.2f}/${goal.max_cost_usd:.2f})",
            )
        if goal.max_continuations and goal.continuation_count >= goal.max_continuations:
            return (
                True,
                f"Max continuations reached ({goal.continuation_count}/{goal.max_continuations})",
            )
        warning: str | None = None
        if goal.max_wall_time_seconds and goal.created_at:
            elapsed = time.time() - goal.created_at
            if elapsed >= 0.8 * goal.max_wall_time_seconds:
                warning = f"Wall time budget at 80% ({int(elapsed)}s/{goal.max_wall_time_seconds}s)"
        if (
            not warning
            and goal.max_total_tokens
            and goal.consumed_tokens >= 0.8 * goal.max_total_tokens
        ):
            warning = f"Token budget at 80% ({goal.consumed_tokens}/{goal.max_total_tokens})"
        elif (
            not warning and goal.max_cost_usd and goal.consumed_cost_usd >= 0.8 * goal.max_cost_usd
        ):
            warning = f"Cost budget at 80% (${goal.consumed_cost_usd:.2f}/${goal.max_cost_usd:.2f})"
        return False, warning
