"""Security and isolation tests for session_artifacts table with PostgreSQL Row-Level Security."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from api.db.models import OrganizationModel, SessionModel, UserModel
from api.db.session import DatabaseSessionManager
from api.services.artifacts import InMemoryArtifactStore, PostgresArtifactStore

from specifications.interfaces.session import SessionArtifact

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.asyncio
async def test_session_artifacts_tenant_isolation_in_memory() -> None:
    store = InMemoryArtifactStore()
    art_a = SessionArtifact(
        id=f"art_{uuid.uuid4().hex[:12]}",
        session_id="s1",
        tenant_org_id="org_a",
        title="Plan A",
        filename="plan.md",
        artifact_type="markdown",
        content="# Content A",
    )
    await store.create_artifact(art_a)

    assert await store.get_artifact("s1", art_a.id, "org_b") is None
    loaded_a = await store.get_artifact("s1", art_a.id, "org_a")
    assert loaded_a is not None
    assert loaded_a.title == "Plan A"
    assert loaded_a.content == "# Content A"


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Requires disposable test PostgreSQL with RLS")
@pytest.mark.asyncio
async def test_session_artifacts_tenant_isolation_postgres() -> None:
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

    store = PostgresArtifactStore(db)
    art_id = f"art_{uuid.uuid4().hex[:12]}"
    art = SessionArtifact(
        id=art_id,
        session_id=session_id,
        tenant_org_id=org_a,
        title="Durable Design",
        filename="design.md",
        artifact_type="markdown",
        content="Architecture design text",
    )
    await store.create_artifact(art)

    # Tenant A sees it
    loaded_a = await store.get_artifact(session_id, art_id, org_a)
    assert loaded_a is not None
    assert loaded_a.title == "Durable Design"

    # Tenant B is blocked by PostgreSQL row-level security
    loaded_b = await store.get_artifact(session_id, art_id, org_b)
    assert loaded_b is None

    # Versions are isolated the same way
    updated = await store.update_artifact(session_id, art_id, org_a, "Second version")
    assert updated is not None and updated.version == 2
    assert [v.version for v in await store.list_versions(session_id, art_id, org_a)] == [1, 2]
    assert await store.list_versions(session_id, art_id, org_b) == []
    assert await store.get_version(session_id, art_id, org_b, 1) is None
    first = await store.get_version(session_id, art_id, org_a, 1)
    assert first is not None and first.content == "Architecture design text"
