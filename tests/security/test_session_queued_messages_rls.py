"""Messages queued during a turn are each organization's own, under forced Row-Level Security."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from api.db.models import (
    OrganizationModel,
    SessionModel,
    SessionQueuedMessageModel,
    UserModel,
)
from api.db.session import DatabaseSessionManager
from api.services.queued_messages import QueuedMessageStore
from sqlalchemy import select

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Requires disposable test PostgreSQL with RLS")
async def test_queued_messages_are_isolated_in_postgres() -> None:
    assert TEST_DATABASE_URL
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    org_a, org_b = (f"org_{uuid.uuid4().hex[:12]}" for _ in range(2))
    session_id = f"sess_{uuid.uuid4().hex[:12]}"
    now = int(time.time())
    for org in (org_a, org_b):
        async with db.session(tenant_org_id=org) as s:
            s.add(OrganizationModel(id=org, name=org, slug=org, created_at=now))
            s.add(
                UserModel(id=f"u_{org}", org_id=org, email=f"{org}@t.io", name="U", created_at=now)
            )
    async with db.session(tenant_org_id=org_a) as s:
        s.add(
            SessionModel(
                id=session_id, org_id=org_a, user_id=f"u_{org_a}", title="A", created_at=now
            )
        )
    store = QueuedMessageStore(db)

    await store.add(org_a, session_id, "m1", "secret plan", "queue", f"u_{org_a}")

    assert await store.pending(org_b, session_id) == []
    assert await store.claim_for_next_turn(org_b, session_id) == []
    assert not await store.cancel(org_b, session_id, "m1")
    async with db.session(tenant_org_id=org_b) as s:
        assert (await s.execute(select(SessionQueuedMessageModel))).scalars().all() == []
    assert [m.id for m in await store.claim_for_next_turn(org_a, session_id)] == ["m1"]
