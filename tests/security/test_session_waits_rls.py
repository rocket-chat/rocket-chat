"""Security and isolation tests for session_waits table with PostgreSQL Row-Level Security."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from api.db.models import OrganizationModel, SessionModel, UserModel
from api.db.session import DatabaseSessionManager
from api.services.waits import InMemoryWaitStore, PostgresWaitStore, SessionWait

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.asyncio
async def test_session_waits_tenant_isolation_in_memory() -> None:
    store = InMemoryWaitStore()
    wait_a = SessionWait(
        id=f"wait_{uuid.uuid4().hex[:12]}",
        session_id="s1",
        tenant_org_id="org_a",
        kind="time",
        target="30s",
        deadline=int(time.time()) + 30,
        status="waiting",
        wake_prompt="Check results",
    )
    await store.create_wait(wait_a)

    assert await store.get_wait("s1", wait_a.id, "org_b") is None
    loaded_a = await store.get_wait("s1", wait_a.id, "org_a")
    assert loaded_a is not None
    assert loaded_a.kind == "time"
    assert loaded_a.wake_prompt == "Check results"

    # list_active_waits
    assert len(await store.list_active_waits("s1", "org_b")) == 0
    list_a = await store.list_active_waits("s1", "org_a")
    assert len(list_a) == 1
    assert list_a[0].id == wait_a.id


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Requires disposable test PostgreSQL with RLS")
@pytest.mark.asyncio
async def test_session_waits_tenant_isolation_postgres() -> None:
    assert TEST_DATABASE_URL is not None
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

    store = PostgresWaitStore(db)
    wait_id = f"wait_{uuid.uuid4().hex[:12]}"
    wait = SessionWait(
        id=wait_id,
        session_id=session_id,
        tenant_org_id=org_a,
        kind="process_exit",
        target="proc_123",
        deadline=now + 600,
        status="waiting",
        wake_prompt="Resume when proc_123 exits",
    )
    await store.create_wait(wait)

    # Tenant A sees it
    loaded_a = await store.get_wait(session_id, wait_id, org_a)
    assert loaded_a is not None
    assert loaded_a.kind == "process_exit"

    # Tenant B is blocked by PostgreSQL row-level security
    loaded_b = await store.get_wait(session_id, wait_id, org_b)
    assert loaded_b is None

    # Tenant B active list is empty
    assert len(await store.list_active_waits(session_id, org_b)) == 0
    assert len(await store.list_active_waits(session_id, org_a)) == 1
