"""Security and isolation tests for session_goals table with PostgreSQL Row-Level Security."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from api.db.models import OrganizationModel, SessionModel, UserModel
from api.db.session import DatabaseSessionManager
from api.services.goals import InMemoryGoalStore, PostgresGoalStore

from specifications.interfaces.session import GoalStatus, SessionGoal

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.asyncio
async def test_session_goals_tenant_isolation_in_memory() -> None:
    store = InMemoryGoalStore()
    goal_a = SessionGoal(
        id=f"goal_{uuid.uuid4().hex[:16]}",
        session_id="s1",
        tenant_org_id="org_a",
        description="Complete feature X",
        status=GoalStatus.ACTIVE,
        max_wall_time_seconds=3600,
        max_total_tokens=100000,
        max_cost_usd=5.0,
    )
    await store.create_goal(goal_a)

    assert await store.get_goal("s1", "org_b") is None
    loaded_a = await store.get_goal("s1", "org_a")
    assert loaded_a is not None
    assert loaded_a.description == "Complete feature X"
    assert loaded_a.status == GoalStatus.ACTIVE


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Requires disposable test PostgreSQL with RLS")
@pytest.mark.asyncio
async def test_session_goals_tenant_isolation_postgres() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    org_a = f"org_{uuid.uuid4().hex[:12]}"
    org_b = f"org_{uuid.uuid4().hex[:12]}"
    session_id = f"sess_{uuid.uuid4().hex[:12]}"

    now = int(time.time())
    async with db.session(tenant_org_id=org_a) as s:
        s.add(OrganizationModel(id=org_a, name="Org A", slug=org_a, created_at=now))
        s.add(
            UserModel(
                id=f"u_{org_a}", org_id=org_a, email="a@test.com", name="User A", created_at=now
            )
        )
        s.add(
            SessionModel(
                id=session_id, org_id=org_a, user_id=f"u_{org_a}", title="Session A", created_at=now
            )
        )

    async with db.session(tenant_org_id=org_b) as s:
        s.add(OrganizationModel(id=org_b, name="Org B", slug=org_b, created_at=now))
        s.add(
            UserModel(
                id=f"u_{org_b}", org_id=org_b, email="b@test.com", name="User B", created_at=now
            )
        )

    store = PostgresGoalStore(db)
    goal = SessionGoal(
        id=f"goal_{uuid.uuid4().hex[:16]}",
        session_id=session_id,
        tenant_org_id=org_a,
        description="Confidential goal for Org A",
        status=GoalStatus.ACTIVE,
        max_wall_time_seconds=1800,
        max_total_tokens=50000,
        max_cost_usd=2.5,
    )
    await store.create_goal(goal)

    # Org B must see nothing under Row-Level Security
    assert await store.get_goal(session_id, org_b) is None

    # Org A sees the goal
    loaded = await store.get_goal(session_id, org_a)
    assert loaded is not None
    assert loaded.description == "Confidential goal for Org A"
    assert loaded.status == GoalStatus.ACTIVE
