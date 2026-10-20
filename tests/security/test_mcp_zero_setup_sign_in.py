"""Signing in to an MCP server needs nothing but its URL: the server describes its OAuth, the
platform sets up a client for its own public callback URL and asks for this server's token."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from api.main import app
from api.routers import mcp_servers
from api.services import mcp_discovery, mcp_engine, mcp_oauth_setup, mcp_turn
from api.services.mcp_discovery import Discovered, Registered
from api.turns.prompt import _mcp_sign_in_note
from fastapi.testclient import TestClient

ME = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "member_user", "X-User-Role": "developer"}
PUBLIC = "https://rocket.example.test"
SERVER = "https://kb.example.test/mcp"


def _found(*, registration: bool = True, metadata_url: bool = False) -> Discovered:
    return Discovered(
        authorization_url="https://kb.example.test/authorize",
        token_url="https://kb.example.test/token",
        registration_url="https://kb.example.test/register" if registration else None,
        scopes="openid email",
        issuer="https://kb.example.test",
        pkce_s256=True,
        resource=SERVER,
        client_metadata_supported=metadata_url,
    )


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    seen: dict[str, Any] = {"registered": [], "token_payloads": [], "found": _found()}

    async def public(_url: str) -> None:
        return None

    async def discover(_url: str, **_: Any) -> Discovered:
        return seen["found"]

    async def register(found: Discovered, redirect_uri: str, **_: Any) -> Registered:
        seen["registered"].append(redirect_uri)
        return Registered(client_id=f"dyn-{len(seen['registered'])}", client_secret=None)

    async def token_endpoint(_url: str, payload: dict[str, str]) -> dict[str, Any]:
        seen["token_payloads"].append(payload)
        return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}

    async def probe(**_: Any) -> tuple[str, list[Any], str | None]:
        return "connected", [], None

    monkeypatch.setenv("ROCKET_PUBLIC_URL", PUBLIC)
    monkeypatch.setattr(mcp_engine, "resolve_public_target", public)
    monkeypatch.setattr(mcp_engine, "_post_token_request", token_endpoint)
    monkeypatch.setattr(mcp_oauth_setup, "discover", discover)
    monkeypatch.setattr(mcp_discovery, "register_client", register)
    from api.routers import mcp_oauth

    monkeypatch.setattr(mcp_oauth, "probe_and_fetch_tools", probe)
    with TestClient(app) as client:
        app.state.db_manager = None
        mcp_turn._mcp_servers_store.clear()
        seen["client"] = client
        yield seen
    mcp_turn._mcp_servers_store.clear()


def _server(auth: dict[str, Any] | None = None) -> str:
    mcp_turn._mcp_servers_store["kb"] = {
        "id": "kb",
        "org_id": "test_org",
        "name": "Knowledge base",
        "transport": "http",
        "endpoint_or_command": SERVER,
        "scope": "user",
        "user_id": "member_user",
        "auth_type": "oauth2",
        "encrypted_auth": dict(auth or {"auth_type": "oauth2"}),
        "tools": [],
        "status": "needs_sign_in",
    }
    return "kb"


def _sign_in_url(client: TestClient) -> dict[str, str]:
    res = client.post("/v1/mcp/servers/kb/oauth/authorize", headers=ME)
    assert res.status_code == 200, res.text
    return {k: v[0] for k, v in parse_qs(urlparse(res.json()["authorization_url"]).query).items()}


def test_a_url_is_enough_and_the_login_comes_back_to_the_public_address(
    setup: dict[str, Any],
) -> None:
    client: TestClient = setup["client"]
    _server()

    params = _sign_in_url(client)

    assert params["redirect_uri"] == f"{PUBLIC}/v1/mcp/oauth/callback", "never the internal address"
    assert params["client_id"] == "dyn-1" and setup["registered"] == [params["redirect_uri"]]
    assert params["resource"] == SERVER and params["scope"] == "openid email"

    done = client.get(
        "/v1/mcp/oauth/callback",
        params={"code": "c", "state": params["state"]},
        headers=ME,
        follow_redirects=False,
    )
    assert "status=connected" in done.headers["location"]
    assert setup["token_payloads"][-1]["resource"] == SERVER
    _sign_in_url(client)
    assert len(setup["registered"]) == 1, "the client is kept while the callback URL is the same"


def test_a_client_registered_for_another_callback_url_is_set_up_again(
    setup: dict[str, Any],
) -> None:
    """Clients registered with the backend's internal address could never complete a login."""
    _server(
        {
            "auth_type": "oauth2",
            "oauth_authorization_url": "https://kb.example.test/authorize",
            "oauth_token_url": "https://kb.example.test/token",
            "oauth_client_id": "registered-for-internal-address",
        }
    )

    params = _sign_in_url(setup["client"])

    assert params["client_id"] == "dyn-1"
    assert setup["registered"] == [f"{PUBLIC}/v1/mcp/oauth/callback"]


def test_a_client_entered_by_hand_is_never_replaced(setup: dict[str, Any]) -> None:
    _server(
        {
            "auth_type": "oauth2",
            "oauth_authorization_url": "https://kb.example.test/authorize",
            "oauth_token_url": "https://kb.example.test/token",
            "oauth_client_id": "my-oauth-app",
            "oauth_client_method": "manual",
        }
    )

    assert _sign_in_url(setup["client"])["client_id"] == "my-oauth-app"
    assert setup["registered"] == []


def test_a_provider_without_self_registration_says_what_to_create(setup: dict[str, Any]) -> None:
    setup["found"] = _found(registration=False)
    _server()

    res = setup["client"].post("/v1/mcp/servers/kb/oauth/authorize", headers=ME)

    assert res.status_code == 400
    assert "create an OAuth app" in res.json()["detail"]
    assert f"{PUBLIC}/v1/mcp/oauth/callback" in res.json()["detail"]


def test_a_provider_that_reads_client_metadata_needs_no_registration(
    setup: dict[str, Any],
) -> None:
    setup["found"] = _found(registration=False, metadata_url=True)
    _server()

    params = _sign_in_url(setup["client"])
    document = setup["client"].get("/v1/mcp/oauth/client-metadata.json")

    assert params["client_id"] == f"{PUBLIC}/v1/mcp/oauth/client-metadata.json"
    assert document.status_code == 200, "public: the provider fetches it without a session"
    assert document.json()["client_id"] == params["client_id"]
    assert document.json()["redirect_uris"] == [params["redirect_uri"]]


def test_checking_a_server_that_asks_for_an_account_offers_sign_in(
    setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refused(**_: Any) -> tuple[str, list[Any], str | None]:
        return "error", [], "HTTP 401"

    async def discover(_url: str) -> Discovered:
        return _found()

    monkeypatch.setattr(mcp_servers, "probe_and_fetch_tools", refused)
    monkeypatch.setattr(mcp_servers, "discover_mcp_oauth", discover)

    res = setup["client"].post(
        "/v1/mcp/servers/validate",
        json={"transport": "http", "endpoint_or_command": SERVER, "scope": "user", "auth": {}},
        headers=ME,
    )

    body = res.json()
    assert body["sign_in"] == "automatic" and body["status"] == "needs_sign_in"
    assert "sign in" in body["error"]


async def test_a_turn_names_the_servers_waiting_for_a_sign_in_instead_of_hiding_them(
    setup: dict[str, Any],
) -> None:
    _server()

    tools = await mcp_turn._mcp_tools_for_turn(None, "test_org", "member_user")

    assert tools.provider is None and tools.needs_sign_in == ["Knowledge base"]
    note = _mcp_sign_in_note(tools.needs_sign_in)
    assert "Knowledge base" in note and f"{PUBLIC}/settings/mcp" in note
