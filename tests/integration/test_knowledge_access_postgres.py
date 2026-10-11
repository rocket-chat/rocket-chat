"""kb_search reads only collections the caller and bound persona may."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from api.db.models import KnowledgeBaseModel
from api.db.session import DatabaseSessionManager
from api.services.knowledge import ingest_document, search_knowledge

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL not configured")


@pytest.fixture
async def manager(tenant_rows: None) -> AsyncIterator[DatabaseSessionManager]:
    db = DatabaseSessionManager(TEST_DB_URL)
    async with db.session(tenant_org_id="test_org") as s:
        for kb_id, name, scope, owner in (
            ("kb-handbook", "Handbook", "org", None),
            ("kb-runbooks", "Runbooks", "org", None),
            ("kb-bobs-notes", "Bob Notes", "user", "member_user"),
        ):
            s.add(
                KnowledgeBaseModel(
                    id=kb_id, org_id="test_org", user_id=owner, scope=scope, name=name
                )
            )
        await s.flush()
        await ingest_document(
            s, "kb-handbook", "test_org", "Vacation", "vacation policy is generous"
        )
        await ingest_document(
            s, "kb-runbooks", "test_org", "Restart", "restart the api with systemctl"
        )
        await ingest_document(
            s, "kb-bobs-notes", "test_org", "Secret", "bob keeps private notes here"
        )
    async with db.session(tenant_org_id="usage_org") as s:
        s.add(
            KnowledgeBaseModel(id="kb-other-org", org_id="usage_org", scope="org", name="Handbook")
        )
        await s.flush()
        await ingest_document(
            s, "kb-other-org", "usage_org", "Foreign", "foreign organization secrets"
        )
    yield db
    await db.close()


async def _search(db: DatabaseSessionManager, user: str, **kwargs: object) -> str:
    async with db.session(tenant_org_id="test_org") as s:
        return await search_knowledge(
            s,
            org_id="test_org",
            user_id=user,
            query_text="policy notes restart",
            **kwargs,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_unscoped_search_covers_org_collections_and_the_callers_own(
    manager: DatabaseSessionManager,
) -> None:
    result = await _search(manager, "member_user")

    assert "Vacation" in result and "Restart" in result and "Secret" in result
    assert "Foreign" not in result


@pytest.mark.asyncio
async def test_another_members_personal_collection_is_never_searched(
    manager: DatabaseSessionManager,
) -> None:
    assert "Secret" not in await _search(manager, "user_1")
    assert "not available" in await _search(manager, "user_1", kb_ref="kb-bobs-notes")


@pytest.mark.asyncio
async def test_a_persona_is_limited_to_its_attached_collections(
    manager: DatabaseSessionManager,
) -> None:
    result = await _search(manager, "member_user", allowed_kb_ids=("kb-runbooks",))

    assert "Restart" in result and "Vacation" not in result and "Secret" not in result
    assert "not available" in await _search(
        manager, "member_user", kb_ref="Handbook", allowed_kb_ids=("kb-runbooks",)
    )


@pytest.mark.asyncio
async def test_a_collection_can_be_selected_by_name_or_id(manager: DatabaseSessionManager) -> None:
    by_name = await _search(manager, "member_user", kb_ref="handbook")
    by_id = await _search(manager, "member_user", kb_ref="kb-handbook")

    assert "Vacation" in by_name and "Restart" not in by_name
    assert by_name == by_id


def test_the_ingest_endpoint_works_under_row_level_security() -> None:
    from api.main import app
    from fastapi.testclient import TestClient

    headers = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "admin_user",
        "X-User-Role": "admin",
    }
    with TestClient(app) as client:
        kb = client.post(
            "/v1/knowledge/bases", json={"name": "API KB", "scope": "org"}, headers=headers
        )
        assert kb.status_code == 200, kb.text
        doc = client.post(
            f"/v1/knowledge/bases/{kb.json()['id']}/documents",
            json={"title": "Doc", "content": "the deploy window is tuesday"},
            headers=headers,
        )
        assert doc.status_code == 200, doc.text
        assert doc.json()["chunk_count"] >= 1
        found = client.post(
            f"/v1/knowledge/bases/{kb.json()['id']}/query",
            json={"query": "deploy window"},
            headers=headers,
        )
        assert found.status_code == 200 and found.json()["results"]
