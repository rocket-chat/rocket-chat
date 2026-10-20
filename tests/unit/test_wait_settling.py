"""Open waits never restart a session whose result arrived or that the person stopped."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from api.main import app
from api.services.waits import InMemoryWaitStore, SessionWait, settle_waits
from api.store import InMemorySessionStore
from httpx import ASGITransport, AsyncClient

ORG, USER = "wait_org", "wait_user"
HEADERS = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": USER}


def _wait(
    wait_id: str, session_id: str, kind: str = "ci_status", target: str = "o/r#1"
) -> SessionWait:
    return SessionWait(
        id=wait_id,
        session_id=session_id,
        tenant_org_id=ORG,
        kind=kind,
        target=target,
        deadline=int(time.time()) + 600,
    )


async def test_only_the_waits_on_the_settled_target_are_closed() -> None:
    store = InMemoryWaitStore()
    for wait in (_wait("w1", "s"), _wait("w2", "s"), _wait("w3", "s", target="o/r#2")):
        await store.create_wait(wait)
    settled = await settle_waits(
        store, "s", ORG, status="completed", kind="ci_status", target="o/r#1"
    )
    assert settled == 2
    assert [w.id for w in await store.list_active_waits("s", ORG)] == ["w3"]


async def test_stopping_a_session_cancels_its_pending_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # session_116c1b0bb3: stopped by its owner, then restarted by a wait expiring 14 min later.
    store, waits = InMemorySessionStore(), InMemoryWaitStore()
    monkeypatch.setattr(app.state, "session_store", store, raising=False)
    monkeypatch.setattr(app.state, "wait_store", waits, raising=False)
    orchestrator = AsyncMock()
    monkeypatch.setattr(app.state, "orchestrator", orchestrator, raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/v1/sessions", json={"title": "CI"}, headers=HEADERS)
        session_id = created.json()["session_id"]
        await waits.create_wait(_wait("w1", session_id))
        stopped = await client.delete(f"/v1/sessions/{session_id}/turns", headers=HEADERS)
    assert stopped.status_code == 200
    orchestrator.cancel_turn.assert_awaited_once_with(session_id)
    assert await waits.list_active_waits(session_id, ORG) == []
    assert (await waits.get_wait(session_id, "w1", ORG)).status == "cancelled"  # type: ignore[union-attr]
