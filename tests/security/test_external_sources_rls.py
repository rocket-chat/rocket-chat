"""external_sources and external_source_links under PostgreSQL row-level security: another
organization sees, changes and deletes nothing, and a periodic claim takes each source once."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from api.db.models import ExternalSourceLinkModel, ExternalSourceModel, OrganizationModel
from api.db.session import DatabaseSessionManager
from api.services.external_sources import PostgresExternalSourceStore
from sqlalchemy import select

from specifications.interfaces.external_sources import (
    Catalogue,
    CatalogueEntry,
    ExternalSource,
    LinkedFile,
    SourceLink,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ADMIN_DATABASE_URL = os.getenv("ADMIN_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="Requires disposable test PostgreSQL with RLS"
)


async def _org(db: DatabaseSessionManager) -> str:
    org = f"org_{uuid.uuid4().hex[:12]}"
    async with db.session(tenant_org_id=org) as s:
        s.add(OrganizationModel(id=org, name=org, slug=org, created_at=int(time.time())))
    return org


def _source(org: str) -> ExternalSource:
    return ExternalSource(
        id=f"src_{uuid.uuid4().hex[:12]}",
        org_id=org,
        name="Agent skills",
        repository="acme/agent-skills",
        ref="main",
        catalogue=Catalogue(
            commit="c1", entries=(CatalogueEntry(kind="skill", path="a", name="a"),)
        ),
        next_sync_at=0,
    )


@pytest.mark.asyncio
async def test_sources_and_links_are_isolated() -> None:
    assert TEST_DATABASE_URL is not None
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    org_a, org_b = await _org(db), await _org(db)
    store = PostgresExternalSourceStore(db)
    source = await store.save_source(_source(org_a))
    await store.save_link(
        SourceLink(
            id=f"lnk_{uuid.uuid4().hex[:12]}",
            org_id=org_a,
            source_id=source.id,
            kind="skill",
            object_id="skill-1",
            path="a",
            files=[LinkedFile("scripts/run.sh", "echo hi", executable=True)],
        )
    )

    assert await store.get_source(org_b, source.id) is None
    assert await store.list_sources(org_b) == []
    assert await store.links_of_source(org_b, source.id) == []
    assert await store.links_of_objects(org_b, "skill", ["skill-1"]) == {}
    assert await store.delete_source(org_b, source.id) is False
    async with db.session(tenant_org_id=org_b) as s:
        # RLS, not a WHERE clause: an unfiltered read as B still sees none of A's rows.
        assert (await s.execute(select(ExternalSourceModel))).scalars().all() == []
        assert (await s.execute(select(ExternalSourceLinkModel))).scalars().all() == []

    stored = await store.get_source(org_a, source.id)
    assert stored is not None and stored.catalogue is not None
    assert stored.catalogue.entry("a") is not None
    links = await store.links_of_objects(org_a, "skill", ["skill-1"])
    assert links["skill-1"].files == [LinkedFile("scripts/run.sh", "echo hi", executable=True)]
    assert await store.delete_source(org_a, source.id) is True
    assert await store.links_of_objects(org_a, "skill", ["skill-1"]) == {}


@pytest.mark.asyncio
@pytest.mark.skipif(not ADMIN_DATABASE_URL, reason="Claims run on the schema-owner connection")
async def test_a_due_source_is_claimed_once() -> None:
    assert TEST_DATABASE_URL is not None and ADMIN_DATABASE_URL is not None
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    admin = DatabaseSessionManager(ADMIN_DATABASE_URL)
    org = await _org(db)
    store = PostgresExternalSourceStore(db, admin)
    source = await store.save_source(_source(org))
    now = int(time.time())

    first = await store.claim_due(now, 900)
    second = await store.claim_due(now, 900)

    assert source.id in {s.id for s in first}
    assert source.id not in {s.id for s in second}
    await admin.close()
