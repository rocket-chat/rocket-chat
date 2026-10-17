"""Security and isolation tests for session_compactions table with PostgreSQL Row-Level Security."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from api.db.models import OrganizationModel, SessionModel, UserModel
from api.db.session import DatabaseSessionManager
from api.services.compaction import InMemoryCompactionStore, PostgresCompactionStore

from specifications.interfaces.compaction import CompactionCheckpoint
from specifications.interfaces.llm import ChatMessage

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.asyncio
async def test_session_compactions_tenant_isolation_in_memory() -> None:
    store = InMemoryCompactionStore()
    cp_a = CompactionCheckpoint(
        id=f"cmp_{uuid.uuid4().hex[:16]}",
        session_id="s1",
        org_id="org_a",
        boundary_seq=10,
        summary="Work done by agent in org a",
        compacted_messages=[ChatMessage(role="user", content="hello")],
        tokens_before=1000,
        tokens_after=300,
        created_at=int(time.time()),
    )
    await store.record_compaction(cp_a)

    assert await store.get_latest_compaction("s1", "org_b") is None
    loaded_a = await store.get_latest_compaction("s1", "org_a")
    assert loaded_a is not None
    assert loaded_a.summary == "Work done by agent in org a"
    assert loaded_a.boundary_seq == 10


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Requires disposable test PostgreSQL with RLS")
@pytest.mark.asyncio
async def test_session_compactions_tenant_isolation_postgres() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    org_a = f"org_{uuid.uuid4().hex[:12]}"
    org_b = f"org_{uuid.uuid4().hex[:12]}"
    session_id = f"sess_{uuid.uuid4().hex[:12]}"

    now = int(time.time())
    # Create parent organizations, users and sessions
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

    store = PostgresCompactionStore(db)
    cp = CompactionCheckpoint(
        id=f"cmp_{uuid.uuid4().hex[:16]}",
        session_id=session_id,
        org_id=org_a,
        boundary_seq=25,
        summary="Summary of work in Org A",
        compacted_messages=[
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Request A"),
        ],
        tokens_before=15000,
        tokens_after=4500,
        created_at=now,
    )
    await store.record_compaction(cp)

    # Org B must see nothing under Row-Level Security
    assert await store.get_latest_compaction(session_id, org_b) is None

    # Org A sees the checkpoint
    loaded = await store.get_latest_compaction(session_id, org_a)
    assert loaded is not None
    assert loaded.boundary_seq == 25
    assert loaded.summary == "Summary of work in Org A"
    assert len(loaded.compacted_messages) == 2
    assert loaded.compacted_messages[1].content == "Request A"
