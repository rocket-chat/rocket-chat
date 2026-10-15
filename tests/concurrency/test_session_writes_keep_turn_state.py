"""A client's change to a session must not erase what a running turn just recorded.

PostgreSQL hands every reader its own copy of the record, so a handler that reads the session,
then writes the whole record back, restores whatever it read: a turn's answer or the active
repository recorded by a clone in between is lost. ``CopyingStore`` behaves like the database
and lands a turn's write between the handler's read and its write.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Callable

import pytest
from api.main import app
from api.services.session_support import ACTIVE_REPOSITORY_KEY
from api.store import InMemorySessionStore
from httpx import ASGITransport, AsyncClient

from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import SessionRecord

OWNER = {"X-Tenant-Org-Id": "org_w", "X-Tenant-User-Id": "owner", "X-User-Role": "developer"}


def _turn_writes(record: SessionRecord) -> None:
    record.metadata = {**record.metadata, ACTIVE_REPOSITORY_KEY: "/workspace/api"}
    record.conversation_history.append(ChatMessage(role="assistant", content="the answer"))


class CopyingStore(InMemorySessionStore):
    """Reads return copies; the turn writes right after the first read."""

    def __init__(self, concurrent_write: Callable[[SessionRecord], None]) -> None:
        super().__init__()
        self._concurrent_write: Callable[[SessionRecord], None] | None = concurrent_write

    async def get_session(
        self, session_id: str, tenant_org_id: str | None = None
    ) -> SessionRecord | None:
        found = await super().get_session(session_id, tenant_org_id)
        snapshot = copy.deepcopy(found)
        if found is not None and self._concurrent_write is not None:
            write, self._concurrent_write = self._concurrent_write, None
            await self.mutate_session(session_id, found.tenant_org_id, write)
        return snapshot


@pytest.fixture
async def api(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[AsyncClient, CopyingStore]]:
    store = CopyingStore(_turn_writes)
    await InMemorySessionStore.create_session(
        store,
        SessionRecord(session_id="s_w", tenant_org_id="org_w", tenant_user_id="owner", title="t"),
    )
    monkeypatch.setattr(app.state, "session_store", store, raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, store


def _turn_state_survived(store: CopyingStore) -> None:
    record = store._sessions["s_w"]
    assert record.metadata[ACTIVE_REPOSITORY_KEY] == "/workspace/api"
    assert [m.content for m in record.conversation_history] == ["the answer"]


async def test_changing_the_permission_mode_keeps_the_turns_writes(
    api: tuple[AsyncClient, CopyingStore],
) -> None:
    client, store = api

    response = await client.patch(
        "/v1/sessions/s_w", json={"title": "New", "metadata": {"permission_mode": "ask"}},
        headers=OWNER,
    )  # fmt: skip

    assert response.status_code == 200, response.text
    _turn_state_survived(store)
    assert store._sessions["s_w"].title == "New"
    assert store._sessions["s_w"].metadata["permission_mode"] == "ask"


async def test_sharing_keeps_the_turns_writes(api: tuple[AsyncClient, CopyingStore]) -> None:
    client, store = api

    response = await client.post(
        "/v1/sessions/s_w/share", json={"is_shared": True, "collaborators": ["bob"]},
        headers=OWNER,
    )  # fmt: skip

    assert response.status_code == 200, response.text
    _turn_state_survived(store)
    assert store._sessions["s_w"].collaborators == ["bob"]
