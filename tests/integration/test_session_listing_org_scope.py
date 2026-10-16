"""The sidebar listing is scoped to the caller's organization and, by default, to the caller.

The cockpit used to append ``tenant_org_id=default_org`` to ``GET /v1/sessions``. For a
user whose organization is anything else that predicate blanked the whole sidebar even
though their sessions existed. No caller can retarget the list: the server uses the
authenticated identity's org. Administrators see their own and shared sessions like everyone
else and ask for ``scope=organization`` to see every member's.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from api.main import app
from api.store import InMemorySessionStore
from httpx import ASGITransport, AsyncClient

from specifications.interfaces.session import SessionRecord

HEADERS = {
    "X-Tenant-Org-Id": "org_a",
    "X-Tenant-User-Id": "user_a",
    "X-User-Role": "developer",
}


ADMIN = {**HEADERS, "X-Tenant-User-Id": "admin_a", "X-User-Role": "admin"}


def _record(
    session_id: str, user: str = "user_a", org: str = "org_a", **metadata: object
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        tenant_org_id=org,
        tenant_user_id=user,
        title=session_id,
        created_at=100,
        updated_at=100,
        metadata=dict(metadata),
    )


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app.state.session_store = InMemorySessionStore()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


async def test_non_admin_org_override_does_not_blank_the_sidebar(client: AsyncClient) -> None:
    store: InMemorySessionStore = app.state.session_store
    await store.create_session(_record("session_mine"))

    # A stale client sends the wrong org; the server must still return the caller's own
    # sessions rather than silently listing nothing.
    response = await client.get("/v1/sessions?tenant_org_id=default_org&limit=50", headers=HEADERS)

    assert response.status_code == 200, response.text
    ids = [item["session_id"] for item in response.json()]
    assert ids == ["session_mine"]


async def _ids(client: AsyncClient, url: str, headers: dict[str, str]) -> list[str]:
    response = await client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    return sorted(item["session_id"] for item in response.json())


async def test_an_admin_sees_own_and_shared_sessions_unless_asking_for_the_organization(
    client: AsyncClient,
) -> None:
    store: InMemorySessionStore = app.state.session_store
    await store.create_session(_record("admins", user="admin_a"))
    await store.create_session(_record("members"))
    await store.create_session(_record("shared", is_shared=True))
    await store.create_session(_record("other_org", user="admin_a", org="org_b"))

    assert await _ids(client, "/v1/sessions", ADMIN) == ["admins", "shared"]
    assert await _ids(client, "/v1/sessions?scope=organization", ADMIN) == [
        "admins",
        "members",
        "shared",
    ]
    # The organization is the identity's, whatever the query says.
    assert await _ids(client, "/v1/sessions?scope=organization&tenant_org_id=org_b", ADMIN) == [
        "admins",
        "members",
        "shared",
    ]


async def test_a_member_cannot_list_the_whole_organization(client: AsyncClient) -> None:
    response = await client.get("/v1/sessions?scope=organization", headers=HEADERS)

    assert response.status_code == 403
