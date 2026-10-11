"""Authentication matrix: who can reach what, and how.

Every case here fails against the pre-hardening behaviour, where tenant headers or the
absence of an identity provider silently produced an administrator.
"""

from __future__ import annotations

import pytest
from api.auth.session_manager import SESSION_COOKIE_NAME, create_session_token
from api.events import AsyncIOEventBus
from api.main import app
from api.store import InMemorySessionStore
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

SPOOFED_ADMIN = {
    "X-Tenant-User-Id": "mallory",
    "X-Tenant-Org-Id": "victim_org",
    "X-User-Role": "admin",
}

PROTECTED_ROUTES = [
    ("GET", "/v1/sessions"),
    ("GET", "/v1/agents"),
    ("GET", "/v1/settings/general"),
    ("GET", "/v1/admin/settings/governance"),
    ("GET", "/v1/admin/users"),
    ("GET", "/v1/memories"),
    ("GET", "/v1/mcp/servers"),
    ("GET", "/v1/tools"),
    ("GET", "/v1/subagents"),
    ("PUT", "/v1/subagents/security_auditor"),
    ("POST", "/v1/sessions/cleanup"),
    ("POST", "/v1/sessions/reset"),
    ("GET", "/v1/slack/status"),
]


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


def _cookie(client: TestClient, *, user: str, org: str, roles: list[str]) -> None:
    client.cookies.set(
        SESSION_COOKIE_NAME, create_session_token(user_id=user, org_id=org, roles=roles)
    )


@pytest.mark.parametrize(("method", "path"), PROTECTED_ROUTES)
def test_anonymous_requests_are_rejected(locked_down: TestClient, method: str, path: str) -> None:
    response = locked_down.request(method, path, json={} if method != "GET" else None)
    assert response.status_code == 401, response.text


@pytest.mark.parametrize(("method", "path"), PROTECTED_ROUTES)
def test_spoofed_tenant_headers_are_ignored(
    locked_down: TestClient, method: str, path: str
) -> None:
    response = locked_down.request(
        method, path, headers=SPOOFED_ADMIN, json={} if method != "GET" else None
    )
    assert response.status_code == 401, response.text


def test_health_stays_public(locked_down: TestClient) -> None:
    assert locked_down.get("/v1/health").status_code == 200


def test_forged_cookie_is_rejected(locked_down: TestClient) -> None:
    import jwt

    forged = jwt.encode(
        {"sub": "mallory", "org_id": "victim_org", "roles": ["admin"], "exp": 4102444800},
        "not-the-server-secret-but-long-enough-0123456789",
        algorithm="HS256",
    )
    locked_down.cookies.set(SESSION_COOKIE_NAME, forged)
    assert locked_down.get("/v1/admin/users").status_code == 401


def test_valid_cookie_authenticates_and_roles_are_enforced(locked_down: TestClient) -> None:
    _cookie(locked_down, user="dev1", org="org_a", roles=["developer"])
    assert locked_down.get("/v1/sessions").status_code == 200
    assert locked_down.get("/v1/admin/settings/general").status_code == 403
    assert locked_down.put("/v1/subagents/security_auditor", json={}).status_code in (403, 422)
    assert locked_down.post("/v1/sessions/cleanup").status_code == 403


def test_dev_login_is_disabled_without_explicit_opt_in(locked_down: TestClient) -> None:
    response = locked_down.post(
        "/api/auth/dev-login",
        json={"username": "mallory", "org_id": "victim_org", "roles": ["admin", "owner"]},
    )
    assert response.status_code == 403
    assert SESSION_COOKIE_NAME not in response.cookies


def test_dev_login_never_grants_client_chosen_roles_without_bypass(
    locked_down: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROCKET_DEV_LOGIN", "true")
    response = locked_down.post(
        "/api/auth/dev-login",
        json={"username": "mallory", "org_id": "victim_org", "roles": ["admin", "owner"]},
    )
    assert response.status_code == 200
    assert response.json()["org_id"] == "default_org"
    me = locked_down.get("/api/auth/me").json()
    assert "admin" not in me["roles"]
    assert me["org_id"] == "default_org"


def test_production_ignores_the_development_bypass(
    locked_down: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    assert locked_down.get("/v1/sessions").status_code == 401
    assert (
        locked_down.post(
            "/api/auth/dev-login", json={"username": "x", "roles": ["admin"]}
        ).status_code
        == 403
    )


def test_websocket_requires_authentication(locked_down: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as closed:
        with locked_down.websocket_connect("/v1/sessions/any/ws"):
            pass
    assert closed.value.code == 1008


def test_websocket_refuses_other_users_private_session(locked_down: TestClient) -> None:
    _cookie(locked_down, user="owner", org="org_a", roles=["developer"])
    created = locked_down.post("/v1/sessions", json={"title": "private"})
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]

    intruder = TestClient(app)
    _cookie(intruder, user="intruder", org="org_a", roles=["developer"])
    with pytest.raises(WebSocketDisconnect) as closed:
        with intruder.websocket_connect(f"/v1/sessions/{session_id}/ws"):
            pass
    assert closed.value.code == 1008

    other_org = TestClient(app)
    _cookie(other_org, user="admin2", org="org_b", roles=["admin", "developer"])
    with pytest.raises(WebSocketDisconnect):
        with other_org.websocket_connect(f"/v1/sessions/{session_id}/ws"):
            pass


def test_websocket_owner_can_connect_and_readonly_cannot_steer(locked_down: TestClient) -> None:
    _cookie(locked_down, user="owner", org="org_a", roles=["developer"])
    session_id = locked_down.post("/v1/sessions", json={"title": "mine"}).json()["session_id"]
    locked_down.post(
        f"/v1/sessions/{session_id}/share", json={"is_shared": True, "collaborators": []}
    )

    viewer = TestClient(app)
    _cookie(viewer, user="viewer", org="org_a", roles=["readonly"])
    with viewer.websocket_connect(f"/v1/sessions/{session_id}/ws") as ws:
        assert ws.receive_json()["type"] == "sync"
        ws.send_json({"type": "share", "is_shared": False, "collaborators": []})
        assert "Forbidden" in ws.receive_json()["detail"]
