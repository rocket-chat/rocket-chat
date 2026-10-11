"""MCP OAuth is bound to the member who started it and stores tokens encrypted."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from api.main import app
from api.services import mcp_engine, mcp_turn
from api.services.mcp_runtime import auth_headers
from config_engine.crypto import CredentialCipher
from fastapi.testclient import TestClient

ADMIN = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "admin_user", "X-User-Role": "admin"}
MEMBER = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "member_user",
    "X-User-Role": "developer",
}
OTHER_ORG_ADMIN = {
    "X-Tenant-Org-Id": "usage_org",
    "X-Tenant-User-Id": "usage_user",
    "X-User-Role": "admin",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    async def public(_url: str) -> None:
        return None

    async def token_endpoint(_url: str, payload: dict[str, str]) -> dict[str, Any]:
        return {"access_token": "at-secret", "refresh_token": "rt-secret", "expires_in": 3600}

    monkeypatch.setattr(mcp_engine, "resolve_public_target", public)
    monkeypatch.setattr(mcp_engine, "_post_token_request", token_endpoint)
    with TestClient(app) as c:
        app.state.db_manager = None
        mcp_turn._mcp_servers_store.clear()
        yield c
    mcp_turn._mcp_servers_store.clear()


def _register_oauth_server(client: TestClient, scope: str, headers: dict[str, str]) -> str:
    mcp_turn._mcp_servers_store["srv-oauth"] = {
        "id": "srv-oauth",
        "org_id": headers["X-Tenant-Org-Id"],
        "name": "Calendar",
        "transport": "http",
        "endpoint_or_command": "https://calendar.example.test/mcp",
        "scope": scope,
        "user_id": headers["X-Tenant-User-Id"] if scope == "user" else None,
        "auth_type": "oauth2",
        "encrypted_auth": {
            "oauth_authorization_url": "https://idp.example.test/authorize",
            "oauth_token_url": "https://idp.example.test/token",
            "oauth_client_id": "rocket",
        },
        "tools": [],
        "status": "offline",
    }
    return "srv-oauth"


def _authorize(client: TestClient, server_id: str, headers: dict[str, str]) -> str:
    res = client.post(f"/v1/mcp/servers/{server_id}/oauth/authorize", headers=headers)
    assert res.status_code == 200, res.text
    return parse_qs(urlparse(res.json()["authorization_url"]).query)["state"][0]


def test_an_admin_connects_an_org_server_and_tokens_are_encrypted_at_rest(
    client: TestClient,
) -> None:
    server_id = _register_oauth_server(client, "org", ADMIN)
    state = _authorize(client, server_id, ADMIN)

    done = client.get(
        "/v1/mcp/oauth/callback",
        params={"code": "c", "state": state},
        headers=ADMIN,
        follow_redirects=False,
    )
    assert "status=connected" in done.headers["location"]

    stored = mcp_turn._mcp_servers_store[server_id]["encrypted_auth"]
    assert "access_token" not in stored and "refresh_token" not in stored
    assert "at-secret" not in str(stored) and "rt-secret" not in str(stored)
    assert auth_headers(stored, CredentialCipher()) == {"Authorization": "Bearer at-secret"}


def test_a_member_cannot_connect_their_account_to_an_org_server(client: TestClient) -> None:
    server_id = _register_oauth_server(client, "org", ADMIN)

    res = client.post(f"/v1/mcp/servers/{server_id}/oauth/authorize", headers=MEMBER)

    assert res.status_code == 403


def test_a_callback_from_a_different_account_is_rejected_and_stores_nothing(
    client: TestClient,
) -> None:
    server_id = _register_oauth_server(client, "org", ADMIN)
    state = _authorize(client, server_id, ADMIN)

    stolen = client.get(
        "/v1/mcp/oauth/callback",
        params={"code": "c", "state": state},
        headers=MEMBER,
        follow_redirects=False,
    )

    assert "status=error" in stolen.headers["location"]
    assert "access_token_enc" not in mcp_turn._mcp_servers_store[server_id]["encrypted_auth"]


def test_the_state_is_single_use(client: TestClient) -> None:
    server_id = _register_oauth_server(client, "org", ADMIN)
    state = _authorize(client, server_id, ADMIN)
    client.get("/v1/mcp/oauth/callback", params={"code": "c", "state": state}, headers=ADMIN)

    replay = client.get(
        "/v1/mcp/oauth/callback",
        params={"code": "c", "state": state},
        headers=ADMIN,
        follow_redirects=False,
    )

    assert "status=error" in replay.headers["location"]


def test_another_organization_cannot_authorize_the_server(client: TestClient) -> None:
    server_id = _register_oauth_server(client, "org", ADMIN)

    res = client.post(f"/v1/mcp/servers/{server_id}/oauth/authorize", headers=OTHER_ORG_ADMIN)

    assert res.status_code == 404


def test_a_member_connects_their_own_personal_server(client: TestClient) -> None:
    server_id = _register_oauth_server(client, "user", MEMBER)
    state = _authorize(client, server_id, MEMBER)

    done = client.get(
        "/v1/mcp/oauth/callback",
        params={"code": "c", "state": state},
        headers=MEMBER,
        follow_redirects=False,
    )

    assert "status=connected" in done.headers["location"]
    assert "access_token_enc" in mcp_turn._mcp_servers_store[server_id]["encrypted_auth"]


@pytest.mark.asyncio
async def test_expiring_tokens_are_refreshed_and_stay_encrypted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def token_endpoint(_url: str, payload: dict[str, str]) -> dict[str, Any]:
        assert payload["grant_type"] == "refresh_token" and payload["refresh_token"] == "rt-old"
        return {"access_token": "at-new", "expires_in": 3600}

    monkeypatch.setattr(mcp_engine, "_post_token_request", token_endpoint)
    cipher = CredentialCipher()
    auth: dict[str, Any] = {
        "oauth_token_url": "https://idp.example.test/token",
        "access_token_enc": cipher.encrypt("at-old"),
        "refresh_token_enc": cipher.encrypt("rt-old"),
        "expires_at": time.time() + 10,
    }

    assert mcp_engine.oauth_token_needs_refresh(auth)
    assert await mcp_engine.refresh_oauth_tokens(auth)
    assert auth_headers(auth, cipher) == {"Authorization": "Bearer at-new"}
    assert not mcp_engine.oauth_token_needs_refresh(auth)
    assert "at-new" not in str(auth)


def test_the_flow_survives_without_any_server_side_state(client: TestClient) -> None:
    """Any replica can finish the login: nothing is remembered in this process."""
    server_id = _register_oauth_server(client, "org", ADMIN)
    state = _authorize(client, server_id, ADMIN)
    assert not hasattr(mcp_engine, "_pending_oauth_states")

    done = client.get(
        "/v1/mcp/oauth/callback",
        params={"code": "c", "state": state},
        headers=ADMIN,
        follow_redirects=False,
    )

    assert "status=connected" in done.headers["location"]


def test_a_callback_without_the_browsers_flow_cookie_is_refused(client: TestClient) -> None:
    server_id = _register_oauth_server(client, "org", ADMIN)
    state = _authorize(client, server_id, ADMIN)
    client.cookies.clear()

    done = client.get(
        "/v1/mcp/oauth/callback",
        params={"code": "c", "state": state},
        headers=ADMIN,
        follow_redirects=False,
    )

    assert "status=error" in done.headers["location"]
    assert "access_token_enc" not in mcp_turn._mcp_servers_store[server_id]["encrypted_auth"]


def test_a_forged_state_is_refused(client: TestClient) -> None:
    server_id = _register_oauth_server(client, "org", ADMIN)
    _authorize(client, server_id, ADMIN)

    done = client.get(
        "/v1/mcp/oauth/callback",
        params={"code": "c", "state": "forged.state.value"},
        headers=ADMIN,
        follow_redirects=False,
    )

    assert "status=error" in done.headers["location"]
