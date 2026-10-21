"""Repository setups are each organization's own, under forced Row-Level Security."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from api.db.models import OrganizationModel, RepositorySetupModel
from api.db.session import DatabaseSessionManager
from api.services.repository_setups import RepositorySetup, RepositorySetupStore
from sqlalchemy import select

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
SETUP = RepositorySetup(repository="acme/api", folder="*", command="make base")


async def test_repository_setups_are_isolated_in_memory() -> None:
    store = RepositorySetupStore(None)
    saved = await store.save("rls_mem_a", SETUP)

    assert await store.list("rls_mem_b") == []
    assert not await store.delete("rls_mem_b", saved.id), "another organization cannot delete it"
    assert [s.id for s in await store.list("rls_mem_a")] == [saved.id]


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Requires disposable test PostgreSQL with RLS")
async def test_repository_setups_are_isolated_in_postgres() -> None:
    assert TEST_DATABASE_URL
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    org_a, org_b = (f"org_{uuid.uuid4().hex[:12]}" for _ in range(2))
    now = int(time.time())
    for org in (org_a, org_b):
        async with db.session(tenant_org_id=org) as s:
            s.add(OrganizationModel(id=org, name=org, slug=org, created_at=now))
    store = RepositorySetupStore(db)

    saved = await store.save(org_a, SETUP)

    assert await store.list(org_b) == []
    assert not await store.delete(org_b, saved.id)
    async with db.session(tenant_org_id=org_b) as s:
        rows = (await s.execute(select(RepositorySetupModel))).scalars().all()
    assert rows == [], "org B's context sees no row of org A, even unfiltered"
    assert [s.id for s in await store.list(org_a)] == [saved.id]
