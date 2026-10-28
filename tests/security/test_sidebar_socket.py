"""The sidebar socket relays only the session updates its member may see."""

from __future__ import annotations

import pytest
from api.auth.session_manager import SESSION_COOKIE_NAME, create_session_token
from api.events import AsyncIOEventBus
from api.main import app
from api.store import InMemorySessionStore
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


@pytest.fixture
def locked_down(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The app as deployed: no development bypass, no trusted proxy headers."""
    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ROCKET_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.delenv("ROCKET_DEV_LOGIN", raising=False)
    monkeypatch.delenv("OIDC_JWKS_URL", raising=False)
    monkeypatch.delenv("OIDC_PUBLIC_KEY", raising=False)
    app.state.session_store = InMemorySessionStore()
    app.state.event_bus = AsyncIOEventBus()
    return TestClient(app)


def _member(user: str, org: str, roles: list[str]) -> TestClient:
    client = TestClient(app)
    client.cookies.set(
        SESSION_COOKIE_NAME, create_session_token(user_id=user, org_id=org, roles=roles)
    )
    return client


def _session(client: TestClient, title: str, *, shared: bool = False) -> str:
    created = client.post("/v1/sessions", json={"title": title})
    assert created.status_code == 200, created.text
    session_id: str = created.json()["session_id"]
    if shared:
        response = client.post(
            f"/v1/sessions/{session_id}/share", json={"is_shared": True, "collaborators": []}
        )
        assert response.status_code == 200, response.text
    return session_id


def _pin(client: TestClient, session_id: str) -> None:
    response = client.patch(f"/v1/sessions/{session_id}/flags", json={"is_pinned": True})
    assert response.status_code == 200, response.text


def test_sidebar_socket_requires_authentication(locked_down: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as closed:
        with locked_down.websocket_connect("/v1/sidebar/ws"):
            pass
    assert closed.value.code == 1008


def test_a_member_hears_their_own_and_shared_sessions_but_not_a_colleagues_private_one(
    locked_down: TestClient,
) -> None:
    owner = _member("owner", "org_a", ["developer"])
    private = _session(owner, "private")
    shared = _session(owner, "shared", shared=True)

    colleague = _member("colleague", "org_a", ["developer"])
    with colleague.websocket_connect("/v1/sidebar/ws") as ws:
        assert ws.receive_json() == {"type": "ready"}
        # Sent first: had it been relayed, it would be the first frame to arrive.
        _pin(owner, private)
        _pin(owner, shared)
        frame = ws.receive_json()
        assert frame["event_type"] == "session_updated"
        assert frame["session_id"] == shared
        assert frame["payload"]["is_pinned"] is True

    with owner.websocket_connect("/v1/sidebar/ws") as ws:
        assert ws.receive_json() == {"type": "ready"}
        own = _session(owner, "another")
        _pin(owner, own)
        assert ws.receive_json()["session_id"] == own


def test_an_administrator_hears_every_session_of_their_organization_only(
    locked_down: TestClient,
) -> None:
    owner = _member("owner", "org_a", ["developer"])
    private = _session(owner, "private")
    elsewhere = _member("someone", "org_b", ["developer"])
    foreign = _session(elsewhere, "foreign")

    admin = _member("admin", "org_a", ["admin", "developer"])
    with admin.websocket_connect("/v1/sidebar/ws") as ws:
        assert ws.receive_json() == {"type": "ready"}
        _pin(elsewhere, foreign)
        _pin(owner, private)
        assert ws.receive_json()["session_id"] == private


def test_a_session_unshared_since_the_socket_opened_is_no_longer_reported(
    locked_down: TestClient,
) -> None:
    owner = _member("owner", "org_a", ["developer"])
    once_shared = _session(owner, "once shared", shared=True)
    still_shared = _session(owner, "still shared", shared=True)

    colleague = _member("colleague", "org_a", ["developer"])
    with colleague.websocket_connect("/v1/sidebar/ws") as ws:
        assert ws.receive_json() == {"type": "ready"}
        unshared = owner.post(
            f"/v1/sessions/{once_shared}/share", json={"is_shared": False, "collaborators": []}
        )
        assert unshared.status_code == 200, unshared.text
        _pin(owner, once_shared)
        _pin(owner, still_shared)
        assert ws.receive_json()["session_id"] == still_shared
