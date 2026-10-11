"""Organization MCP servers where every member connects their own account."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from api.main import app
from api.services import mcp_engine, mcp_turn
from api.services.mcp_member_tokens import McpMemberTokenStore
from api.services.mcp_runtime import build_targets
from config_engine.crypto import CredentialCipher
from fastapi.testclient import TestClient

ORG = "test_org"
ADMIN = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "mt_admin", "X-User-Role": "admin"}
ALICE = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "mt_alice", "X-User-Role": "developer"}
BOB = {**ALICE, "X-Tenant-User-Id": "mt_bob"}
VIEWER = {**ALICE, "X-Tenant-User-Id": "mt_viewer", "X-User-Role": "readonly"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    issued: list[str] = []

    async def public(_url: str) -> None:
        return None

    async def token_endpoint(_url: str, payload: dict[str, str]) -> dict[str, Any]:
        issued.append(payload["code"])
        return {"access_token": f"at-{payload['code']}", "refresh_token": "rt", "expires_in": 3600}

    monkeypatch.setattr(mcp_engine, "resolve_public_target", public)
    monkeypatch.setattr(mcp_engine, "_post_token_request", token_endpoint)
    with TestClient(app) as c:
        app.state.db_manager = None
        app.state.mcp_member_tokens = McpMemberTokenStore(None)
        app.state.mcp_member_tokens._memory.clear()
        mcp_turn._mcp_servers_store.clear()
        mcp_turn._mcp_servers_store["srv-linear"] = {
            "id": "srv-linear",
            "org_id": ORG,
            "name": "Linear",
            "transport": "http",
            "endpoint_or_command": "https://mcp.linear.example/mcp",
            "scope": "org",
            "auth_type": "oauth2",
            "encrypted_auth": {
                "oauth_mode": "member",
                "oauth_authorization_url": "https://idp.example/authorize",
                "oauth_token_url": "https://idp.example/token",
                "oauth_client_id": "rocket",
            },
            "tools": [],
            "status": "connected",
        }
        yield c
    mcp_turn._mcp_servers_store.clear()


def _connect(client: TestClient, headers: dict[str, str], code: str) -> int:
    started = client.post("/v1/mcp/servers/srv-linear/oauth/authorize", headers=headers)
    if started.status_code != 200:
        return started.status_code
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    done = client.get(
        "/v1/mcp/oauth/callback",
        params={"code": code, "state": state},
        headers=headers,
        follow_redirects=False,
    )
    assert "status=connected" in done.headers["location"], done.headers["location"]
    return 200


def _status_for(client: TestClient, headers: dict[str, str]) -> str:
    servers = client.get("/v1/mcp/servers", headers=headers).json()
    return str(next(s for s in servers if s["id"] == "srv-linear")["oauth_status"])


def test_any_member_connects_their_own_account_and_sees_only_their_own_status(
    client: TestClient,
) -> None:
    assert _status_for(client, ALICE) == "pending" and _status_for(client, BOB) == "pending"
    assert _connect(client, ALICE, "alice-code") == 200
    assert _status_for(client, ALICE) == "connected"
    assert _status_for(client, BOB) == "pending", "Alice's account does not connect Bob's"
    assert _status_for(client, ADMIN) == "pending"


def test_tokens_are_kept_per_member_encrypted_and_never_on_the_shared_server(
    client: TestClient,
) -> None:
    _connect(client, ALICE, "alice-code")
    _connect(client, BOB, "bob-code")

    shared = mcp_turn._mcp_servers_store["srv-linear"]["encrypted_auth"]
    assert not any(k in shared for k in ("access_token_enc", "access_token", "refresh_token_enc"))

    store: McpMemberTokenStore = app.state.mcp_member_tokens
    alice = asyncio.run(store.get(ORG, "srv-linear", "mt_alice"))
    bob = asyncio.run(store.get(ORG, "srv-linear", "mt_bob"))
    assert alice and bob and alice["access_token_enc"] != bob["access_token_enc"]
    assert "alice-code" not in str(alice), "stored encrypted, not in the clear"
    cipher = CredentialCipher()
    assert cipher.decrypt(alice["access_token_enc"]) == "at-alice-code"
    assert cipher.decrypt(bob["access_token_enc"]) == "at-bob-code"


def test_read_only_members_cannot_connect_an_account(client: TestClient) -> None:
    assert _connect(client, VIEWER, "x") == 403


def test_a_shared_org_server_is_still_for_administrators_only(client: TestClient) -> None:
    mcp_turn._mcp_servers_store["srv-linear"]["encrypted_auth"].pop("oauth_mode")
    assert _connect(client, ALICE, "alice-code") == 403
    assert _connect(client, ADMIN, "admin-code") == 200


def test_a_member_cannot_complete_someone_elses_authorization(client: TestClient) -> None:
    started = client.post("/v1/mcp/servers/srv-linear/oauth/authorize", headers=ALICE)
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    stolen = client.get(
        "/v1/mcp/oauth/callback",
        params={"code": "c", "state": state},
        headers=BOB,
        follow_redirects=False,
    )
    assert "status=error" in stolen.headers["location"]
    assert asyncio.run(app.state.mcp_member_tokens.get(ORG, "srv-linear", "mt_bob")) is None


def test_each_members_turn_runs_with_their_own_token_or_without_the_server(
    client: TestClient,
) -> None:
    _connect(client, ALICE, "alice-code")
    _connect(client, BOB, "bob-code")
    row = mcp_turn._mcp_servers_store["srv-linear"]

    async def targets_for(user: str) -> dict[str, str]:
        rows = await mcp_turn._rows_with_member_tokens(None, ORG, user, [row])
        built = build_targets(
            rows,
            cipher=CredentialCipher(),
            allowed_org_transports=["http"],
            allowed_user_transports=[],
        )
        return {t.server_id: t.headers.get("Authorization", "") for t in built}

    async def run() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        return (
            await targets_for("mt_alice"),
            await targets_for("mt_bob"),
            await targets_for("mt_new"),
        )

    alice, bob, newcomer = asyncio.run(run())
    assert alice == {"srv-linear:mt_alice": "Bearer at-alice-code"}
    assert bob == {"srv-linear:mt_bob": "Bearer at-bob-code"}
    assert newcomer == {}, "a member who has not connected gets no tools, and no one else's token"
