"""Watches on PostgreSQL: they persist, and an organization never sees another's."""

from __future__ import annotations

import os
import uuid

import pytest
from api.db.session import current_tenant_org_id
from api.main import app
from api.services.github_watches import GithubWatchStore
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs PostgreSQL")

HEADERS = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "watch_db_user",
    "X-User-Role": "admin",
}


def test_a_watch_survives_a_round_trip_and_is_invisible_to_another_organization() -> None:
    repo = f"acme/watch-{uuid.uuid4().hex[:6]}"
    with TestClient(app) as client:
        session_id = client.post(
            "/v1/sessions", json={"title": "follows a pr"}, headers=HEADERS
        ).json()["session_id"]
        manager = app.state.db_manager

        async def scenario() -> tuple[list[str], list[str], list[dict[str, object]], list[str]]:
            current_tenant_org_id.set("test_org")
            store = GithubWatchStore(manager)
            watch = await store.watch("test_org", session_id, repo, pr_number=7, user_id="u")
            await store.record_delivery(
                watch, {"summary": "CI failed", "at": 1}, woke=False, finished=False
            )
            mine = [w.id for w in await store.matching("test_org", repo, (7,), None)]
            theirs = [w.id for w in await store.matching("usage_org", repo, (7,), None)]
            pending = await store.take_pending("test_org", session_id)
            await store.unwatch("test_org", session_id, repo)
            after = [w.id for w in await store.matching("test_org", repo, (7,), None)]
            return mine, theirs, pending, after

        mine, theirs, pending, after = client.portal.call(scenario)
        client.delete(f"/v1/sessions/{session_id}", headers=HEADERS)

    assert len(mine) == 1 and theirs == []
    assert [e["summary"] for e in pending] == ["CI failed"]
    assert after == []
