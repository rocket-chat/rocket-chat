"""Two writers on one session must not erase each other (PostgreSQL, row-locked mutation).

A turn appends its answer while the title task renames the session. With whole-record
``update_session`` the later write restored whatever its own earlier read had seen, so on a
database the answer could vanish from the stored history (the cockpit then showed none after
the title arrived). ``mutate_session`` serialises them.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from api.db.session import current_tenant_org_id
from api.main import app
from fastapi.testclient import TestClient

from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import SessionRecord

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs PostgreSQL")

HEADERS = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "writer_user",
    "X-User-Role": "developer",
}


def test_a_rename_that_started_before_the_answer_does_not_erase_it() -> None:
    with TestClient(app) as client:
        created = client.post("/v1/sessions", json={"title": "race"}, headers=HEADERS)
        session_id = created.json()["session_id"]
        store = app.state.session_store

        async def overlapping_writers() -> SessionRecord:
            current_tenant_org_id.set("test_org")
            started = asyncio.Event()

            async def old_style_rename() -> None:
                # Read first, write after the answer landed: the pre-fix behaviour.
                stale = await store.get_session(session_id)
                assert stale is not None
                started.set()
                await asyncio.sleep(0.3)
                stale.title = "Renamed"
                await store.mutate_session(
                    session_id,
                    stale.tenant_org_id,
                    lambda record: setattr(record, "title", "Renamed"),
                )

            async def answer() -> None:
                await started.wait()
                await store.mutate_session(
                    session_id,
                    "test_org",
                    lambda record: record.conversation_history.append(
                        ChatMessage(role="assistant", content="the answer")
                    ),
                )

            await asyncio.gather(old_style_rename(), answer())
            final = await store.get_session(session_id)
            assert final is not None
            return final

        final = client.portal.call(overlapping_writers)

    assert final.title == "Renamed"
    assert [m.content for m in final.conversation_history] == ["the answer"]


def test_many_concurrent_appends_all_survive() -> None:
    with TestClient(app) as client:
        session_id = client.post(
            "/v1/sessions", json={"title": f"many-{uuid.uuid4().hex[:4]}"}, headers=HEADERS
        ).json()["session_id"]
        store = app.state.session_store

        async def append_many() -> SessionRecord:
            current_tenant_org_id.set("test_org")
            await asyncio.gather(
                *(
                    store.mutate_session(
                        session_id,
                        "test_org",
                        lambda record, n=n: record.conversation_history.append(
                            ChatMessage(role="user", content=f"m{n}")
                        ),
                    )
                    for n in range(12)
                )
            )
            final = await store.get_session(session_id)
            assert final is not None
            return final

        final = client.portal.call(append_many)

    assert sorted(m.content for m in final.conversation_history) == sorted(
        f"m{n}" for n in range(12)
    )
