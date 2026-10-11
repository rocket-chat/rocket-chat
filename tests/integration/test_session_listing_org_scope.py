"""The sidebar listing must be scoped to the caller's organization.

The cockpit used to append ``tenant_org_id=default_org`` to ``GET /v1/sessions``. For a
user whose organization is anything else that predicate blanked the whole sidebar even
though their sessions existed. A non-admin caller can no longer retarget the list: the
server uses the authenticated identity's org and ignores the client-supplied override.
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


def _record(session_id: str) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        tenant_org_id="org_a",
        tenant_user_id="user_a",
        title=session_id,
        created_at=100,
        updated_at=100,
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
