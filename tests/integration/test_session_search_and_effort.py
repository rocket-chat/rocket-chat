"""Transcript search over a session's whole history, and the reasoning effort saved on a session.

Both go through the real ASGI routes with the in-memory store: the search must find messages
the cockpit has not loaded and never another tenant's; the effort is validated where it enters
(create and PATCH) and can be cleared.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from api.routers.session_crud import router as sessions_router
from api.routers.session_search import MAX_MATCHES
from api.routers.session_search import router as search_router
from api.store import InMemorySessionStore
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import ChatMessage
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
OTHER_MEMBER = {
    "X-Tenant-Org-Id": "default_org",
    "X-Tenant-User-Id": "someone_else",
    "X-User-Role": "developer",
}


class _Bus:
    async def publish(self, event: object) -> None:
        return None


@pytest.fixture
async def client() -> AsyncIterator[tuple[AsyncClient, InMemorySessionStore]]:
    app = FastAPI()
    store = InMemorySessionStore()
    app.state.session_store = store
    app.state.sandbox_driver = FakeSandboxDriver()
    app.state.event_bus = _Bus()
    app.include_router(search_router)
    app.include_router(sessions_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, store


async def _session_with(store: InMemorySessionStore, messages: list[ChatMessage]) -> str:
    record = SessionRecord(
        session_id="session_search",
        tenant_org_id="default_org",
        tenant_user_id="dev_user",
        title="Search me",
        conversation_history=messages,
    )
    await store.create_session(record)
    return record.session_id


@pytest.mark.asyncio
async def test_search_finds_messages_anywhere_in_the_history(
    client: tuple[AsyncClient, InMemorySessionStore],
) -> None:
    ac, store = client
    history = [ChatMessage(role="user", content="Fix the Login redirect")]
    history += [ChatMessage(role="assistant", content=f"Step {i}") for i in range(500)]
    history.append(ChatMessage(role="assistant", content="The login page now redirects. login!"))
    session_id = await _session_with(store, history)

    res = await ac.get(f"/v1/sessions/{session_id}/search", params={"q": "LOGIN"}, headers=HEADERS)

    assert res.status_code == 200, res.text
    body = res.json()
    assert [m["index"] for m in body["matches"]] == [0, 501]
    assert body["total_messages"] == 502 and body["truncated"] is False
    first, last = body["matches"]
    assert first["role"] == "user"
    found = first["excerpt"][first["match_start"] : first["match_start"] + first["match_length"]]
    assert found.lower() == "login"
    assert last["occurrences"] == 2


@pytest.mark.asyncio
async def test_a_long_message_gives_a_short_single_line_excerpt(
    client: tuple[AsyncClient, InMemorySessionStore],
) -> None:
    ac, store = client
    content = ("x" * 5000) + "\nneedle\n" + ("y" * 5000)
    session_id = await _session_with(store, [ChatMessage(role="assistant", content=content)])

    res = await ac.get(f"/v1/sessions/{session_id}/search", params={"q": "needle"}, headers=HEADERS)

    match = res.json()["matches"][0]
    assert len(match["excerpt"]) <= 200
    assert "\n" not in match["excerpt"]
    assert match["excerpt"].startswith("…") and match["excerpt"].endswith("…")
    start = match["match_start"]
    assert match["excerpt"][start : start + match["match_length"]] == "needle"


@pytest.mark.asyncio
async def test_matches_are_capped_and_say_so(
    client: tuple[AsyncClient, InMemorySessionStore],
) -> None:
    ac, store = client
    history = [ChatMessage(role="assistant", content="same text") for _ in range(MAX_MATCHES + 50)]
    session_id = await _session_with(store, history)

    res = await ac.get(
        f"/v1/sessions/{session_id}/search",
        params={"q": "same", "limit": MAX_MATCHES},
        headers=HEADERS,
    )

    body = res.json()
    assert len(body["matches"]) == MAX_MATCHES and body["truncated"] is True
    too_many = await ac.get(
        f"/v1/sessions/{session_id}/search",
        params={"q": "same", "limit": MAX_MATCHES + 1},
        headers=HEADERS,
    )
    assert too_many.status_code == 422


@pytest.mark.asyncio
async def test_search_never_reads_a_session_the_caller_may_not_see(
    client: tuple[AsyncClient, InMemorySessionStore],
) -> None:
    ac, store = client
    session_id = await _session_with(store, [ChatMessage(role="user", content="secret plan")])

    other_org = await ac.get(
        f"/v1/sessions/{session_id}/search", params={"q": "secret"}, headers=OTHER_TENANT
    )
    other_member = await ac.get(
        f"/v1/sessions/{session_id}/search", params={"q": "secret"}, headers=OTHER_MEMBER
    )

    assert other_org.status_code == 404
    assert other_member.status_code == 403
    empty = await ac.get(f"/v1/sessions/{session_id}/search", params={"q": ""}, headers=HEADERS)
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_a_session_keeps_the_reasoning_effort_chosen_for_it(
    client: tuple[AsyncClient, InMemorySessionStore],
) -> None:
    ac, store = client
    created = await ac.post(
        "/v1/sessions",
        json={"title": "Effort", "metadata": {"reasoning_effort": "low"}},
        headers=HEADERS,
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]
    assert created.json()["metadata"]["reasoning_effort"] == "low"

    patched = await ac.patch(
        f"/v1/sessions/{session_id}",
        json={"metadata": {"reasoning_effort": "high"}},
        headers=HEADERS,
    )
    assert patched.status_code == 200, patched.text
    record = await store.get_session(session_id)
    assert record is not None and record.metadata["reasoning_effort"] == "high"

    cleared = await ac.patch(
        f"/v1/sessions/{session_id}", json={"metadata": {"reasoning_effort": None}}, headers=HEADERS
    )
    assert cleared.status_code == 200
    assert cleared.json()["metadata"]["reasoning_effort"] is None


@pytest.mark.asyncio
async def test_an_unknown_reasoning_effort_is_refused(
    client: tuple[AsyncClient, InMemorySessionStore],
) -> None:
    ac, _store = client
    created = await ac.post(
        "/v1/sessions",
        json={"title": "Effort", "metadata": {"reasoning_effort": "turbo"}},
        headers=HEADERS,
    )
    assert created.status_code == 422

    ok = await ac.post("/v1/sessions", json={"title": "Effort"}, headers=HEADERS)
    session_id = ok.json()["session_id"]
    patched = await ac.patch(
        f"/v1/sessions/{session_id}",
        json={"metadata": {"reasoning_effort": "xhigh"}},
        headers=HEADERS,
    )
    assert patched.status_code == 422
