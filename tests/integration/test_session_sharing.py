"""Sharing a session by public link, and what the link shows."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from api.auth import share_token
from api.main import app
from api.store import InMemorySessionStore
from fastapi.testclient import TestClient

from specifications.interfaces.llm import ChatMessage

ORG = "share_org"
ADMIN = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "admin_user", "X-User-Role": "admin"}
OWNER = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "owner_user", "X-User-Role": "developer"}
OTHER = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "other_user", "X-User-Role": "developer"}


@pytest.fixture
def client() -> TestClient:
    app.state.session_store = InMemorySessionStore()
    return TestClient(app)


def _enable_links(client: TestClient, enabled: bool = True) -> None:
    saved = client.put(
        "/v1/admin/settings/general",
        json={"config": {"public_session_links": enabled}},
        headers=ADMIN,
    )
    assert saved.status_code == 200, saved.text


async def _conversation(session_id: str) -> None:
    store = app.state.session_store
    record = await store.get_session(session_id)
    record.conversation_history = [
        ChatMessage(role="user", content="Fix the build"),
        ChatMessage(
            role="assistant",
            content="Done - I changed the config.",
            reasoning="private thoughts",
            tool_calls=[{"id": "1", "function": {"name": "bash_exec", "arguments": "{}"}}],
        ),
        ChatMessage(role="tool", content="SECRET=abc123", tool_call_id="1"),
    ]


def _new_session(client: TestClient) -> str:
    created = client.post("/v1/sessions", json={"title": "Build fix"}, headers=OWNER)
    assert created.status_code in (200, 201), created.text
    session_id: str = created.json()["session_id"]
    asyncio.run(_conversation(session_id))
    return session_id


@pytest.mark.setting("general.public_session_links")
def test_public_links_exist_only_when_the_organization_allows_them(client: TestClient) -> None:
    session_id = _new_session(client)
    _enable_links(client, False)

    refused = client.post(f"/v1/sessions/{session_id}/sharing/link", json={}, headers=OWNER)
    assert refused.status_code == 403

    _enable_links(client, True)
    created = client.post(f"/v1/sessions/{session_id}/sharing/link", json={}, headers=OWNER)
    assert created.status_code == 200 and created.json()["public_link"]["url"].startswith(
        "/shared/"
    )
    token = created.json()["public_link"]["url"].removeprefix("/shared/")
    assert client.get(f"/v1/shared/{token}").status_code == 200

    _enable_links(client, False)
    assert client.get(f"/v1/shared/{token}").status_code == 404, "switching it off ends every link"


def test_a_public_link_shows_the_conversation_and_nothing_private(client: TestClient) -> None:
    session_id = _new_session(client)
    _enable_links(client)
    link = client.post(
        f"/v1/sessions/{session_id}/sharing/link", json={"expires_in_days": 3}, headers=OWNER
    ).json()["public_link"]

    opened = client.get(f"/v1/shared{link['url'].removeprefix('/shared')}")  # no credentials

    assert opened.status_code == 200
    body = opened.json()
    assert body["title"] == "Build fix"
    assert body["messages"] == [
        {"role": "user", "content": "Fix the build"},
        {"role": "assistant", "content": "Done - I changed the config."},
    ]
    text = opened.text
    assert "SECRET" not in text and "private thoughts" not in text and "bash_exec" not in text
    assert opened.headers["cache-control"] == "no-store"
    assert "noindex" in opened.headers["x-robots-tag"]


def test_revoking_or_replacing_a_link_ends_the_old_one(client: TestClient) -> None:
    session_id = _new_session(client)
    _enable_links(client)
    first = client.post(f"/v1/sessions/{session_id}/sharing/link", json={}, headers=OWNER).json()
    first_token = first["public_link"]["url"].removeprefix("/shared/")
    second = client.post(f"/v1/sessions/{session_id}/sharing/link", json={}, headers=OWNER).json()
    second_token = second["public_link"]["url"].removeprefix("/shared/")

    assert client.get(f"/v1/shared/{first_token}").status_code == 404
    assert client.get(f"/v1/shared/{second_token}").status_code == 200

    revoked = client.delete(f"/v1/sessions/{session_id}/sharing/link", headers=OWNER)
    assert revoked.status_code == 200 and revoked.json()["public_link"] is None
    assert client.get(f"/v1/shared/{second_token}").status_code == 404


def test_expired_forged_and_foreign_tokens_are_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = _new_session(client)
    _enable_links(client)
    link = client.post(f"/v1/sessions/{session_id}/sharing/link", json={}, headers=OWNER).json()
    token = link["public_link"]["url"].removeprefix("/shared/")

    assert client.get(f"/v1/shared/{token}x").status_code == 404
    assert client.get("/v1/shared/not-a-token").status_code == 404
    preview_like, _ = share_token.mint("other", ORG, "id", 1)
    assert client.get(f"/v1/shared/{preview_like}").status_code == 404

    real_time = time.time
    monkeypatch.setattr(share_token.time, "time", lambda: real_time() - 40 * 86400)
    stale, _ = share_token.mint(session_id, ORG, "any", 7)
    monkeypatch.undo()
    assert client.get(f"/v1/shared/{stale}").status_code == 404


def test_only_the_owner_or_an_admin_manages_sharing(client: TestClient) -> None:
    session_id = _new_session(client)
    _enable_links(client)

    assert client.get(f"/v1/sessions/{session_id}/sharing", headers=OTHER).status_code == 403
    assert (
        client.post(f"/v1/sessions/{session_id}/sharing/link", json={}, headers=OTHER).status_code
        == 403
    )
    assert client.get(f"/v1/sessions/{session_id}/sharing", headers=ADMIN).status_code == 200


def test_sharing_with_the_whole_organization_lets_members_open_it(client: TestClient) -> None:
    session_id = _new_session(client)
    assert client.get(f"/v1/sessions/{session_id}", headers=OTHER).status_code == 403

    shared: Any = client.put(
        f"/v1/sessions/{session_id}/sharing",
        json={"org_wide": True, "emails": []},
        headers=OWNER,
    )

    assert shared.status_code == 200 and shared.json()["org_wide"] is True
    assert client.get(f"/v1/sessions/{session_id}", headers=OTHER).status_code == 200
