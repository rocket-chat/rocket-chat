"""Integration tests for cross-session, tenant-scoped memory recall.

Proves the trustworthiness contract of long-term memory: a fact remembered in one session
is recalled in a later, unrelated session, and an organisation can never recall — or even
observe — another organisation's memories. Runs against PostgreSQL because the value of the
test is the real pgvector cosine search under Row-Level Security.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from api.db.models import OrganizationModel, SessionModel, UserModel
from api.db.session import DatabaseSessionManager
from api.main import app
from api.routers.memory import router as memory_router
from api.services.knowledge import generate_deterministic_embedding
from api.services.memory import MemoryService
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

ADMIN_DB_URL = os.getenv("ADMIN_DATABASE_URL", "")
TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")


def _deterministic_embedder():
    async def _embed(content: str) -> list[float]:
        return generate_deterministic_embedding(content)

    return _embed


@pytest.fixture
async def admin_manager(
    require_database: Callable[[Exception, str], None],
) -> AsyncIterator[DatabaseSessionManager]:
    url = ADMIN_DB_URL or TEST_DB_URL
    if not url:
        pytest.skip("ADMIN_DATABASE_URL/TEST_DATABASE_URL not configured")
    manager = DatabaseSessionManager(url)
    try:
        async with manager.session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as err:
        require_database(err, url)
    yield manager
    await manager.close()


@pytest.fixture
async def app_manager(
    require_database: Callable[[Exception, str], None],
) -> AsyncIterator[DatabaseSessionManager]:
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not configured")
    manager = DatabaseSessionManager(TEST_DB_URL)
    try:
        async with manager.session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as err:
        require_database(err, TEST_DB_URL)
    yield manager
    await manager.close()


async def _seed_tenant(
    admin: DatabaseSessionManager, org_id: str, user_id: str, session_id: str
) -> None:
    """Provision an org, a user and one session so the memory foreign keys resolve."""
    async with admin.session() as session:
        session.add(OrganizationModel(id=org_id, name=org_id, slug=org_id.replace("_", "-")))
        session.add(
            UserModel(id=user_id, org_id=org_id, email=f"{user_id}@rocket.chat", name=user_id)
        )
        await session.flush()
        session.add(SessionModel(id=session_id, org_id=org_id, user_id=user_id, title=session_id))


@pytest.mark.asyncio
async def test_cross_session_recall_returns_memory_and_is_tenant_scoped(
    admin_manager: DatabaseSessionManager, app_manager: DatabaseSessionManager
) -> None:
    suffix = uuid.uuid4().hex[:6]
    org_a, org_b = f"org_a_{suffix}", f"org_b_{suffix}"
    user_a, user_b = f"user_a_{suffix}", f"user_b_{suffix}"
    session_a, session_b = f"sess_a_{suffix}", f"sess_b_{suffix}"
    await _seed_tenant(admin_manager, org_a, user_a, session_a)
    await _seed_tenant(admin_manager, org_b, user_b, session_b)

    service = MemoryService(app_manager, embedder=_deterministic_embedder())
    fact = "Production deploys run at 09:00 UTC on Tuesdays."

    outcome = await service.remember(
        org_id=org_a,
        user_id=user_a,
        content=fact,
        scope="org",
        source_session_id=session_a,
    )
    assert outcome.status == "created"

    # A later, unrelated session recalls it (recall is not scoped to the source session).
    recalled = await service.recall(org_id=org_a, user_id=user_a, query=fact)
    assert [record.content for record in recalled] == [fact]

    # A different organisation asking the exact same question sees nothing.
    assert await service.recall(org_id=org_b, user_id=user_b, query=fact) == []
    assert await service.list_memories(org_id=org_b, user_id=user_b) == []


@pytest.mark.asyncio
async def test_dedupe_updates_instead_of_inserting(
    admin_manager: DatabaseSessionManager, app_manager: DatabaseSessionManager
) -> None:
    suffix = uuid.uuid4().hex[:6]
    org_id, user_id, session_id = f"org_d_{suffix}", f"user_d_{suffix}", f"sess_d_{suffix}"
    await _seed_tenant(admin_manager, org_id, user_id, session_id)

    service = MemoryService(app_manager, embedder=_deterministic_embedder())
    content = "The team prefers trunk-based development."

    first = await service.remember(org_id=org_id, user_id=user_id, content=content)
    second = await service.remember(org_id=org_id, user_id=user_id, content=content)

    assert first.status == "created"
    assert second.status == "updated"
    assert second.record is not None and first.record is not None
    assert second.record.id == first.record.id
    assert len(await service.list_memories(org_id=org_id, user_id=user_id)) == 1


@pytest.mark.asyncio
async def test_forget_removes_memory_for_owner_only(
    admin_manager: DatabaseSessionManager, app_manager: DatabaseSessionManager
) -> None:
    suffix = uuid.uuid4().hex[:6]
    org_id, user_id, session_id = f"org_f_{suffix}", f"user_f_{suffix}", f"sess_f_{suffix}"
    await _seed_tenant(admin_manager, org_id, user_id, session_id)

    service = MemoryService(app_manager, embedder=_deterministic_embedder())
    outcome = await service.remember(
        org_id=org_id, user_id=user_id, content="Remember to purge this.", scope="user"
    )
    assert outcome.record is not None

    # A different user in the same org cannot forget another user's private memory.
    assert (
        await service.forget(org_id=org_id, user_id="someone_else", memory_id=outcome.record.id)
        is False
    )
    assert await service.forget(org_id=org_id, user_id=user_id, memory_id=outcome.record.id) is True
    assert await service.list_memories(org_id=org_id, user_id=user_id) == []


@pytest.mark.asyncio
async def test_session_summary_round_trip(
    admin_manager: DatabaseSessionManager, app_manager: DatabaseSessionManager
) -> None:
    suffix = uuid.uuid4().hex[:6]
    org_id, user_id, session_id = f"org_s_{suffix}", f"user_s_{suffix}", f"sess_s_{suffix}"
    await _seed_tenant(admin_manager, org_id, user_id, session_id)

    service = MemoryService(app_manager, embedder=_deterministic_embedder())
    await service.write_summary(
        session_id=session_id,
        org_id=org_id,
        summary_text="Refactored the deploy pipeline and added a retry.",
        turn_start=1,
        turn_end=3,
    )
    await service.write_summary(
        session_id=session_id,
        org_id=org_id,
        summary_text="Refactored the deploy pipeline and added a retry with backoff.",
        turn_start=1,
        turn_end=4,
    )

    summary = await service.get_summary(session_id=session_id, org_id=org_id)
    assert summary == "Refactored the deploy pipeline and added a retry with backoff."

    # Another tenant cannot read the summary even by primary key.
    assert await service.get_summary(session_id=session_id, org_id=f"org_other_{suffix}") is None


@pytest.mark.asyncio
async def test_recall_touches_last_used_at(
    admin_manager: DatabaseSessionManager, app_manager: DatabaseSessionManager
) -> None:
    suffix = uuid.uuid4().hex[:6]
    org_id, user_id, session_id = f"org_t_{suffix}", f"user_t_{suffix}", f"sess_t_{suffix}"
    await _seed_tenant(admin_manager, org_id, user_id, session_id)

    service = MemoryService(app_manager, embedder=_deterministic_embedder())
    content = "Run the release checklist before tagging."
    await service.remember(org_id=org_id, user_id=user_id, content=content)
    before = (await service.list_memories(org_id=org_id, user_id=user_id))[0].last_used_at

    await service.recall(org_id=org_id, user_id=user_id, query=content)

    after = (await service.list_memories(org_id=org_id, user_id=user_id))[0].last_used_at
    assert before is None
    assert after is not None and after >= int(time.time()) - 5


@pytest.mark.asyncio
async def test_memory_endpoints_list_and_forget(
    admin_manager: DatabaseSessionManager, app_manager: DatabaseSessionManager
) -> None:
    """The cockpit inspector contract: list the caller's memories, then forget one."""
    suffix = uuid.uuid4().hex[:6]
    org_id, user_id, session_id = f"org_e_{suffix}", f"user_e_{suffix}", f"sess_e_{suffix}"
    await _seed_tenant(admin_manager, org_id, user_id, session_id)

    service = MemoryService(app_manager, embedder=_deterministic_embedder())
    outcome = await service.remember(
        org_id=org_id, user_id=user_id, content="The release train leaves on Thursdays."
    )
    assert outcome.record is not None

    app.state.db_manager = app_manager
    if not any(getattr(route, "path", "") == "/v1/memories" for route in app.routes):
        app.include_router(memory_router)

    headers = {
        "X-Tenant-Org-Id": org_id,
        "X-Tenant-User-Id": user_id,
        "X-User-Role": "developer",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listing = await client.get("/v1/memories", headers=headers)
        assert listing.status_code == 200
        body = listing.json()
        assert body["total"] == 1
        assert body["memories"][0]["content"] == "The release train leaves on Thursdays."
        assert body["memories"][0]["scope"] == "user"

        forgotten = await client.delete(f"/v1/memories/{outcome.record.id}", headers=headers)
        assert forgotten.status_code == 200
        assert forgotten.json()["forgotten"] is True

        after = await client.get("/v1/memories", headers=headers)
        assert after.json()["total"] == 0

        # A read-only role cannot forget memories.
        readonly = await client.delete(
            f"/v1/memories/{outcome.record.id}",
            headers={**headers, "X-User-Role": "readonly"},
        )
        assert readonly.status_code == 403
