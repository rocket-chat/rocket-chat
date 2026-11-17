"""session_files under PostgreSQL row-level security: another organization sees nothing."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from api.db.models import OrganizationModel, SessionModel, UserModel
from api.db.session import DatabaseSessionManager
from api.services.session_files import PostgresSessionFileStore, SessionFileLimits

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Requires disposable test PostgreSQL with RLS")
@pytest.mark.asyncio
async def test_session_files_are_isolated_and_bounded_in_postgres() -> None:
    assert TEST_DATABASE_URL is not None
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    org_a = f"org_{uuid.uuid4().hex[:12]}"
    org_b = f"org_{uuid.uuid4().hex[:12]}"
    session_id = f"sess_{uuid.uuid4().hex[:12]}"
    now = int(time.time())
    async with db.session(tenant_org_id=org_a) as s:
        s.add(OrganizationModel(id=org_a, name="Org A", slug=org_a, created_at=now))
        s.add(
            UserModel(id=f"u_{org_a}", org_id=org_a, email="a@test.com", name="A", created_at=now)
        )
        s.add(
            SessionModel(
                id=session_id, org_id=org_a, user_id=f"u_{org_a}", title="A", created_at=now
            )
        )

    store = PostgresSessionFileStore(
        db, SessionFileLimits(max_session_bytes=2_500, max_file_bytes=1_000)
    )
    await store.put(session_id, org_a, ".rocket/outputs/1_a.txt", "a" * 1_000)
    await store.put(session_id, org_a, ".rocket/outputs/2_b.txt", "b" * 1_000)
    written = await store.put(session_id, org_a, ".rocket/outputs/3_c.txt", "c" * 1_000)

    assert written.evicted == (".rocket/outputs/1_a.txt",)
    assert [f.path for f in await store.list_files(session_id, org_a)] == [
        ".rocket/outputs/3_c.txt",
        ".rocket/outputs/2_b.txt",
    ]
    assert await store.get(session_id, org_b, ".rocket/outputs/3_c.txt") is None
    assert await store.list_files(session_id, org_b) == []
    assert not await store.delete(session_id, org_b, ".rocket/outputs/3_c.txt")
    kept = await store.get(session_id, org_a, ".rocket/outputs/3_c.txt")
    assert kept is not None and kept.content == "c" * 1_000
