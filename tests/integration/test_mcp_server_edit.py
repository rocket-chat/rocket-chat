"""Editing a registered MCP server: what is kept, what must be retyped and who may do it."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from api.main import app
from api.services import mcp_turn
from api.services.mcp_turn import _cipher
from httpx import ASGITransport, AsyncClient

ORG = "edit_org"
ADMIN = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "admin_user", "X-User-Role": "admin"}
ALICE = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "alice", "X-User-Role": "developer"}
TOOLS = [{"name": "echo", "description": "Echo"}]


@pytest.fixture(autouse=True)
def in_memory(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(app.state, "db_manager", None, raising=False)
    monkeypatch.setattr(app.state, "mcp_member_tokens", None, raising=False)
    mcp_turn._mcp_servers_store.clear()
    yield
    mcp_turn._mcp_servers_store.clear()


@pytest.fixture
async def api() -> Iterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _command_server(**changes: Any) -> dict[str, Any]:
    server: dict[str, Any] = {
        "name": "Release binary",
        "transport": "stdio",
        "endpoint_or_command": "tool serve",
        "scope": "org",
        "tools": TOOLS,
        "auth": {
            "auth_type": "custom_headers",
            "install_command": "curl -L https://example.test/tool > tool",
            "headers": [
                {"name": "API_KEY", "value": "org-secret", "is_secret": True},
                {"name": "REGION", "value": "eu", "is_secret": False},
            ],
        },
    }
    return {**server, **changes}


def _stored_headers(server_id: str) -> dict[str, dict[str, Any]]:
    auth = mcp_turn._mcp_servers_store[server_id]["encrypted_auth"]
    return {h["name"]: h for h in auth["headers"]}


async def test_an_admin_edits_a_command_server_without_retyping_its_secret(
    api: AsyncClient,
) -> None:
    created = (await api.post("/v1/mcp/servers", json=_command_server(), headers=ADMIN)).json()

    edit = _command_server(id=created["id"], endpoint_or_command="tool serve --fast")
    edit["auth"]["install_command"] = "curl -L https://example.test/v2 > tool"
    edit["auth"]["headers"] = [
        {"name": "API_KEY", "value": None, "is_secret": True},
        {"name": "REGION", "value": "us", "is_secret": False},
    ]
    saved = await api.post("/v1/mcp/servers", json=edit, headers=ADMIN)

    assert saved.status_code == 200
    assert saved.json()["endpoint_or_command"] == "tool serve --fast"
    assert saved.json()["install_command"].endswith("/v2 > tool")
    headers = _stored_headers(created["id"])
    assert _cipher.decrypt(headers["API_KEY"]["value"]) == "org-secret"
    assert headers["REGION"]["value"] == "us"


async def test_a_new_secret_value_replaces_the_stored_one(api: AsyncClient) -> None:
    created = (await api.post("/v1/mcp/servers", json=_command_server(), headers=ADMIN)).json()
    edit = _command_server(id=created["id"])
    edit["auth"]["headers"][0]["value"] = "rotated"

    await api.post("/v1/mcp/servers", json=edit, headers=ADMIN)

    assert _cipher.decrypt(_stored_headers(created["id"])["API_KEY"]["value"]) == "rotated"


async def test_a_variable_added_in_an_edit_must_have_a_value(api: AsyncClient) -> None:
    created = (await api.post("/v1/mcp/servers", json=_command_server(), headers=ADMIN)).json()
    edit = _command_server(id=created["id"])
    edit["auth"]["headers"].append({"name": "NEW_VAR", "value": "", "is_secret": True})

    rejected = await api.post("/v1/mcp/servers", json=edit, headers=ADMIN)

    assert rejected.status_code == 422 and "NEW_VAR" in rejected.json()["detail"]


async def test_members_keep_their_own_values_when_the_server_is_edited(api: AsyncClient) -> None:
    server = _command_server()
    server["auth"]["headers"][0] = {
        "name": "API_KEY",
        "value": "",
        "is_secret": True,
        "member_override": True,
    }
    created = (await api.post("/v1/mcp/servers", json=server, headers=ADMIN)).json()
    await api.put(
        f"/v1/mcp/servers/{created['id']}/env", json={"values": {"API_KEY": "mine"}}, headers=ALICE
    )

    edit = {**server, "id": created["id"], "endpoint_or_command": "tool serve --fast"}
    await api.post("/v1/mcp/servers", json=edit, headers=ADMIN)

    view = (await api.get(f"/v1/mcp/servers/{created['id']}/env", headers=ALICE)).json()
    assert view["variables"] == [{"name": "API_KEY", "has_default": False, "overridden": True}]


async def test_a_member_cannot_edit_an_organization_server(api: AsyncClient) -> None:
    created = (await api.post("/v1/mcp/servers", json=_command_server(), headers=ADMIN)).json()
    edit = _command_server(id=created["id"], endpoint_or_command="evil")
    edit["auth"]["headers"][0]["value"] = None

    denied = await api.post("/v1/mcp/servers", json=edit, headers=ALICE)

    assert denied.status_code == 403
    stored = mcp_turn._mcp_servers_store[created["id"]]
    assert stored["endpoint_or_command"] == "tool serve"


async def test_an_http_server_that_moves_must_have_its_secret_retyped(api: AsyncClient) -> None:
    server = {
        "name": "Remote",
        "transport": "http",
        "endpoint_or_command": "https://mcp.example.test/a",
        "scope": "org",
        "tools": TOOLS,
        "auth": {"auth_type": "api_key", "api_key": "remote-secret"},
    }
    created = (await api.post("/v1/mcp/servers", json=server, headers=ADMIN)).json()

    same_host = {**server, "id": created["id"], "auth": {"auth_type": "api_key"}}
    kept = await api.post("/v1/mcp/servers", json=same_host, headers=ADMIN)
    assert kept.json()["has_api_key"] is True

    moved = {**same_host, "endpoint_or_command": "https://elsewhere.example.test/a"}
    after = await api.post("/v1/mcp/servers", json=moved, headers=ADMIN)
    assert after.json()["has_api_key"] is False


def _remote_server(**auth: Any) -> dict[str, Any]:
    return {
        "name": "Remote",
        "transport": "http",
        "endpoint_or_command": "https://mcp.example.test/a",
        "scope": "org",
        "tools": TOOLS,
        "auth": auth,
    }


async def test_the_response_carries_what_an_edit_form_needs(api: AsyncClient) -> None:
    created = await api.post(
        "/v1/mcp/servers",
        json=_remote_server(
            auth_type="oauth2",
            oauth_authorization_url="https://auth.example.test/authorize",
            oauth_token_url="https://auth.example.test/token",
            oauth_client_id="client-1",
            oauth_client_secret="client-secret",
            oauth_scopes=["read", "write"],
        ),
        headers=ADMIN,
    )
    body = created.json()
    assert body["oauth_authorization_url"] == "https://auth.example.test/authorize"
    assert body["oauth_token_url"] == "https://auth.example.test/token"
    assert body["oauth_client_id"] == "client-1"
    assert body["oauth_scopes"] == ["read", "write"]
    assert body["has_oauth_secret"] is True and "client-secret" not in created.text


async def test_a_remote_server_keeps_its_oauth_account_unless_it_is_pointed_elsewhere(
    api: AsyncClient,
) -> None:
    oauth = {
        "auth_type": "oauth2",
        "oauth_authorization_url": "https://auth.example.test/authorize",
        "oauth_token_url": "https://auth.example.test/token",
        "oauth_client_id": "client-1",
    }
    created = (
        await api.post("/v1/mcp/servers", json=_remote_server(**oauth), headers=ADMIN)
    ).json()
    record = mcp_turn._mcp_servers_store[created["id"]]
    record["encrypted_auth"]["access_token_enc"] = _cipher.encrypt("account-token")

    from api.services.mcp_member_tokens import McpMemberTokenStore

    await McpMemberTokenStore(None).put(ORG, created["id"], "alice", {"access_token_enc": "x"})

    rename = {**_remote_server(**oauth), "id": created["id"], "name": "Renamed"}
    await api.post("/v1/mcp/servers", json=rename, headers=ADMIN)
    assert await McpMemberTokenStore(None).get(ORG, created["id"], "alice") is not None

    moved = {**rename, "endpoint_or_command": "https://elsewhere.example.test/a"}
    await api.post("/v1/mcp/servers", json=moved, headers=ADMIN)
    assert await McpMemberTokenStore(None).get(ORG, created["id"], "alice") is None


async def test_validating_an_edit_uses_the_stored_secret_and_obeys_the_same_access_rules(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = (
        await api.post(
            "/v1/mcp/servers",
            json=_remote_server(auth_type="api_key", api_key="remote-secret"),
            headers=ADMIN,
        )
    ).json()
    seen: dict[str, Any] = {}

    async def fake_probe(**kwargs: Any) -> tuple[str, list[dict[str, Any]], str | None]:
        seen.update(kwargs)
        return "connected", [], None

    monkeypatch.setattr("api.routers.mcp_servers.probe_and_fetch_tools", fake_probe)
    body = {
        "id": created["id"],
        "transport": "http",
        "endpoint_or_command": "https://mcp.example.test/a",
        "scope": "org",
        "auth": {"auth_type": "api_key"},
    }

    allowed = await api.post("/v1/mcp/servers/validate", json=body, headers=ADMIN)
    assert allowed.json()["valid"] is True
    assert seen["headers"]["Authorization"] == "Bearer remote-secret"

    seen.clear()
    denied = await api.post("/v1/mcp/servers/validate", json=body, headers=ALICE)
    assert denied.json()["valid"] is False and not seen, "a member's test sent nothing anywhere"
