"""Integration tests for session fork and listing ergonomics.

Exercises the real ASGI surface with the deterministic in-memory store and fake sandbox:
a fork preserves the parent's history and later diverges, pin/archive survive a listing
round-trip, keyset paging over HTTP visits every session exactly once, and the listing
router can replace the legacy full-dump handler (it is included ahead of it here, exactly
as the application bootstrap must).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from api.db.session import DatabaseSessionManager
from api.db.store import PostgresSessionStore
from api.routers.fork import router as fork_router
from api.routers.session_crud import router as sessions_router
from api.routers.sessions_list import NEXT_CURSOR_HEADER
from api.routers.sessions_list import router as sessions_list_router
from api.store import InMemorySessionStore
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.sandbox import WorkspaceSpec
from specifications.interfaces.session import SessionRecord

HEADERS = {
    "X-Tenant-Org-Id": "default_org",
    "X-Tenant-User-Id": "dev_user",
    "X-User-Role": "developer",
}
OTHER_TENANT = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "test_user",
    "X-User-Role": "developer",
}


class CapturingBus:
    """Minimal event bus that records published events for assertions."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


@pytest.fixture
async def client() -> AsyncIterator[
    tuple[AsyncClient, InMemorySessionStore, CapturingBus, FakeSandboxDriver]
]:
    test_app = FastAPI()
    store = InMemorySessionStore()
    bus = CapturingBus()
    driver = FakeSandboxDriver()
    test_app.state.session_store = store
    test_app.state.sandbox_driver = driver
    test_app.state.event_bus = bus
    # Listing must win over the legacy handler for GET /v1/sessions, so it is included
    # first: FastAPI resolves routes in registration order.
    test_app.include_router(sessions_list_router)
    test_app.include_router(fork_router)
    test_app.include_router(sessions_router)
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        yield ac, store, bus, driver


async def _create_session(client: AsyncClient, title: str = "Parent") -> str:
    res = await client.post("/v1/sessions", json={"title": title}, headers=HEADERS)
    assert res.status_code == 200, res.text
    return str(res.json()["session_id"])


@pytest.mark.asyncio
async def test_fork_preserves_history_and_isolates_later_turns(
    client: tuple[AsyncClient, InMemorySessionStore, CapturingBus, FakeSandboxDriver],
) -> None:
    ac, _store, _bus, _driver = client
    parent_id = await _create_session(ac, "Forkable Parent")
    parent = await _store.get_session(parent_id)
    parent.conversation_history = [
        ChatMessage(role="user", content="first question"),
        ChatMessage(role="assistant", content="first answer"),
        ChatMessage(role="user", content="second question"),
        ChatMessage(role="assistant", content="second answer"),
    ]
    await _store.update_session(parent)

    fork = await ac.post(
        f"/v1/sessions/{parent_id}/fork",
        json={"up_to_turn": 1},
        headers=HEADERS,
    )
    assert fork.status_code == 200, fork.text
    child = fork.json()
    assert child["session_id"] != parent_id
    assert [m["content"] for m in child["conversation_history"]] == [
        "first question",
        "first answer",
    ]
    assert child["forked_from"] == {"session_id": parent_id, "turn": 1}

    # A later turn on the child must not touch the parent's history.
    await ac.patch(
        f"/v1/sessions/{child['session_id']}",
        json={"conversation_history": [{"role": "user", "content": "child only"}]},
        headers=HEADERS,
    )
    parent_after = await ac.get(f"/v1/sessions/{parent_id}", headers=HEADERS)
    assert [m["content"] for m in parent_after.json()["conversation_history"]] == [
        "first question",
        "first answer",
        "second question",
        "second answer",
    ]


@pytest.mark.asyncio
async def test_fork_records_workspace_origin(
    client: tuple[AsyncClient, InMemorySessionStore, CapturingBus, FakeSandboxDriver],
) -> None:
    ac, store, _bus, driver = client
    parent_id = await _create_session(ac, "Workspace Parent")
    parent = await store.get_session(parent_id)
    assert parent is not None
    # Simulate a parent workspace with tracked files before forking.
    await driver.ensure_workspace(
        WorkspaceSpec(
            session_id=parent_id,
            tenant_org_id=parent.tenant_org_id,
            tenant_user_id=parent.tenant_user_id,
            container_image="fake:latest",
        )
    )

    fork = await ac.post(f"/v1/sessions/{parent_id}/fork", json={}, headers=HEADERS)
    assert fork.status_code == 200, fork.text
    body = fork.json()
    assert body["metadata"]["workspace_origin"] == "files"
    assert body["metadata"]["forked_from"]["session_id"] == parent_id


@pytest.mark.asyncio
async def test_pin_and_archive_round_trip_and_broadcast(
    client: tuple[AsyncClient, InMemorySessionStore, CapturingBus, FakeSandboxDriver],
) -> None:
    ac, _store, bus, _driver = client
    session_id = await _create_session(ac, "Pinnable")

    pinned = await ac.patch(
        f"/v1/sessions/{session_id}/flags",
        json={"is_pinned": True},
        headers=HEADERS,
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["is_pinned"] is True
    assert any(
        getattr(event, "event_type", None) and event.event_type.value == "session_updated"
        for event in bus.events
    )

    listed = await ac.get("/v1/sessions", headers=HEADERS)
    assert listed.status_code == 200
    entry = next(s for s in listed.json() if s["session_id"] == session_id)
    assert entry["is_pinned"] is True

    archived = await ac.patch(
        f"/v1/sessions/{session_id}/flags",
        json={"is_archived": True},
        headers=HEADERS,
    )
    assert archived.status_code == 200
    default_list = await ac.get("/v1/sessions", headers=HEADERS)
    assert session_id not in {s["session_id"] for s in default_list.json()}
    with_archived = await ac.get("/v1/sessions", params={"archived": "true"}, headers=HEADERS)
    assert session_id in {s["session_id"] for s in with_archived.json()}


@pytest.mark.asyncio
async def test_keyset_listing_over_http_has_no_skips_or_duplicates(
    client: tuple[AsyncClient, InMemorySessionStore, CapturingBus, FakeSandboxDriver],
) -> None:
    ac, _store, _bus, _driver = client
    created = {await _create_session(ac, f"Session {index}") for index in range(5)}

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        params: dict[str, str | int] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        res = await ac.get("/v1/sessions", params=params, headers=HEADERS)
        assert res.status_code == 200, res.text
        seen.extend(s["session_id"] for s in res.json())
        cursor = res.headers.get(NEXT_CURSOR_HEADER)
        if not cursor:
            break

    assert set(seen) == created
    assert len(seen) == len(set(seen))


@pytest.mark.asyncio
async def test_search_filters_by_title(
    client: tuple[AsyncClient, InMemorySessionStore, CapturingBus, FakeSandboxDriver],
) -> None:
    ac, _store, _bus, _driver = client
    auth_id = await _create_session(ac, "Refactor authentication")
    await _create_session(ac, "Write deployment docs")

    res = await ac.get("/v1/sessions", params={"q": "auth"}, headers=HEADERS)
    assert res.status_code == 200
    assert {s["session_id"] for s in res.json()} == {auth_id}


@pytest.mark.asyncio
async def test_fork_is_tenant_isolated(
    client: tuple[AsyncClient, InMemorySessionStore, CapturingBus, FakeSandboxDriver],
) -> None:
    ac, _store, _bus, _driver = client
    parent_id = await _create_session(ac, "Private Parent")

    fork = await ac.post(f"/v1/sessions/{parent_id}/fork", json={}, headers=OTHER_TENANT)
    assert fork.status_code in (403, 404)

    listed = await ac.get("/v1/sessions", headers=OTHER_TENANT)
    assert parent_id not in {s["session_id"] for s in listed.json()}


@pytest.mark.asyncio
async def test_invalid_cursor_is_a_visible_400(
    client: tuple[AsyncClient, InMemorySessionStore, CapturingBus, FakeSandboxDriver],
) -> None:
    ac, _store, _bus, _driver = client
    res = await ac.get("/v1/sessions", params={"cursor": "!!!"}, headers=HEADERS)
    assert res.status_code == 400


# --- PostgreSQL path: the keyset SQL and flag persistence -----------------------------


@pytest.fixture
async def postgres_store(
    require_database: Callable[[Exception, str], None],
) -> AsyncIterator[PostgresSessionStore]:
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured")
    manager = DatabaseSessionManager(url)
    try:
        from sqlalchemy import text

        async with manager.session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as err:
        require_database(err, url)
    yield PostgresSessionStore(manager)
    await manager.close()


def _pg_record(session_id: str, updated_at: int) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        tenant_org_id="default_org",
        tenant_user_id="dev_user",
        title=session_id,
        updated_at=updated_at,
        created_at=updated_at,
    )


@pytest.mark.asyncio
async def test_postgres_keyset_and_flags(postgres_store: PostgresSessionStore) -> None:
    from specifications.interfaces.session import decode_session_cursor

    for session_id, updated_at in [
        ("pg_session_1", 100),
        ("pg_session_2", 100),
        ("pg_session_3", 100),
        ("pg_session_4", 99),
        ("pg_session_5", 98),
    ]:
        await postgres_store.create_session(_pg_record(session_id, updated_at))

    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = await postgres_store.list_sessions_page("default_org", limit=2, cursor=cursor)
        seen.extend(record.session_id for record in page.records)
        if page.next_cursor is None:
            break
        cursor = decode_session_cursor(page.next_cursor)

    assert seen == ["pg_session_3", "pg_session_2", "pg_session_1", "pg_session_4", "pg_session_5"]

    updated = await postgres_store.set_session_flags(
        "pg_session_1", "default_org", is_pinned=True, is_archived=True
    )
    assert updated is not None and updated.is_pinned and updated.is_archived
    default_page = await postgres_store.list_sessions_page("default_org", limit=50)
    assert "pg_session_1" not in {r.session_id for r in default_page.records}
    archived_page = await postgres_store.list_sessions_page(
        "default_org", include_archived=True, limit=50
    )
    assert "pg_session_1" in {r.session_id for r in archived_page.records}


@pytest.mark.asyncio
async def test_postgres_search(postgres_store: PostgresSessionStore) -> None:
    await postgres_store.create_session(_pg_record("pg_auth", 100))
    await postgres_store.create_session(_pg_record("pg_docs", 101))
    page = await postgres_store.list_sessions_page("default_org", query="auth", limit=50)
    assert {r.session_id for r in page.records} == {"pg_auth"}


@pytest.mark.asyncio
async def test_postgres_non_admin_visibility(postgres_store: PostgresSessionStore) -> None:
    """The JSONB owner/shared/collaborator filter must deny a private foreign session."""
    await postgres_store.create_session(
        SessionRecord(
            session_id="pg_vis_mine",
            tenant_org_id="default_org",
            tenant_user_id="dev_user",
            title="mine",
            updated_at=100,
            created_at=100,
        )
    )
    await postgres_store.create_session(
        SessionRecord(
            session_id="pg_vis_shared",
            tenant_org_id="default_org",
            tenant_user_id="someone_else",
            title="shared",
            is_shared=True,
            metadata={"is_shared": True},
            updated_at=100,
            created_at=100,
        )
    )
    await postgres_store.create_session(
        SessionRecord(
            session_id="pg_vis_private",
            tenant_org_id="default_org",
            tenant_user_id="someone_else",
            title="private",
            updated_at=100,
            created_at=100,
        )
    )

    page = await postgres_store.list_sessions_page(
        "default_org", visible_to_user_id="dev_user", limit=50
    )
    assert {r.session_id for r in page.records} == {"pg_vis_mine", "pg_vis_shared"}
