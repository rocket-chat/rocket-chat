"""Missions on real PostgreSQL: tenant isolation and exactly-once claiming."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator

import pytest
from api.db.models import OrganizationModel, UserModel
from api.db.session import DatabaseSessionManager
from api.services.missions import Mission, MissionStore, claim_due
from sqlalchemy import text

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")
ADMIN_DB_URL = os.getenv("ADMIN_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not (TEST_DB_URL and ADMIN_DB_URL), reason="TEST/ADMIN_DATABASE_URL not configured"
)


@pytest.fixture
async def managers() -> AsyncIterator[tuple[DatabaseSessionManager, DatabaseSessionManager]]:
    app_db, admin_db = DatabaseSessionManager(TEST_DB_URL), DatabaseSessionManager(ADMIN_DB_URL)
    yield app_db, admin_db
    await app_db.close()
    await admin_db.close()


async def _tenant(admin_db: DatabaseSessionManager) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    org, user = f"org_ms_{suffix}", f"user_ms_{suffix}"
    async with admin_db.session() as session:
        session.add(OrganizationModel(id=org, name=org, slug=org.replace("_", "-")))
        await session.flush()
        session.add(UserModel(id=user, org_id=org, email=f"{user}@x.test", name=user))
    return org, user


def _mission(org: str, user: str, name: str = "Nightly") -> Mission:
    return Mission(id="", org_id=org, user_id=user, name=name, prompt="Triage", cron="0 3 * * *")


async def test_missions_are_invisible_across_organizations(
    managers: tuple[DatabaseSessionManager, DatabaseSessionManager],
) -> None:
    app_db, admin_db = managers
    (org_a, user_a), (org_b, user_b) = await _tenant(admin_db), await _tenant(admin_db)
    store = MissionStore(app_db)
    created = await store.create(_mission(org_a, user_a))
    await store.create(_mission(org_b, user_b, "Other"))

    assert [m.id for m in await store.list(org_a, None)] == [created.id]
    assert await store.get(org_b, created.id) is None
    assert await store.delete(org_b, created.id) is False
    assert await store.delete(org_a, created.id) is True


async def test_replicas_racing_for_a_due_mission_start_it_once(
    managers: tuple[DatabaseSessionManager, DatabaseSessionManager],
) -> None:
    app_db, admin_db = managers
    org, user = await _tenant(admin_db)
    store = MissionStore(app_db)
    mission = await store.create(_mission(org, user))
    async with admin_db.session() as session:
        await session.execute(
            text("UPDATE scheduled_missions SET next_run_at = :past WHERE id = :id"),
            {"past": int(time.time()) - 60, "id": mission.id},
        )

    batches = await asyncio.gather(*(claim_due(admin_db, store) for _ in range(6)))
    mine = [m for batch in batches for m in batch if m.id == mission.id]
    assert len(mine) == 1

    reread = await store.get(org, mission.id)
    assert reread is not None and reread.next_run_at is not None
    assert reread.next_run_at > time.time()
    assert not [m for m in await claim_due(admin_db, store) if m.id == mission.id]


async def test_the_last_run_is_recorded_under_the_tenant(
    managers: tuple[DatabaseSessionManager, DatabaseSessionManager],
) -> None:
    app_db, admin_db = managers
    org, user = await _tenant(admin_db)
    store = MissionStore(app_db)
    mission = await store.create(_mission(org, user))
    await store.mark_run(org, mission.id, "session_abc", "started")
    reread = await store.get(org, mission.id)
    assert reread is not None
    assert (reread.last_session_id, reread.last_status) == ("session_abc", "started")
