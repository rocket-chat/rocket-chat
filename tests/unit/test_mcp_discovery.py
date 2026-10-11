"""MCP OAuth discovery (RFC 9728 / 8414) and dynamic registration (RFC 7591) via the SDK helpers."""

from __future__ import annotations

import json

import httpx2
import pytest
from api.services import mcp_discovery
from api.services.mcp_discovery import DiscoveryError, discover, register_client

SERVER = "https://mcp.example.com/mcp"
AUTH = "https://auth.example.com"


@pytest.fixture(autouse=True)
def _public(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    guarded: list[str] = []

    async def allow(url: str) -> None:
        guarded.append(url)

    monkeypatch.setattr(mcp_discovery, "resolve_public_target", allow)
    return guarded


def _client(
    handler: httpx2.MockTransport | None = None, **routes: httpx2.Response
) -> httpx2.AsyncClient:
    def respond(request: httpx2.Request) -> httpx2.Response:
        return routes.get(str(request.url), httpx2.Response(404))

    return httpx2.AsyncClient(transport=handler or httpx2.MockTransport(respond))


def _full_server(
    *, registration: bool = True, pkce: list[str] | None = None
) -> dict[str, httpx2.Response]:
    metadata: dict[str, object] = {
        "issuer": AUTH,
        "authorization_endpoint": f"{AUTH}/authorize",
        "token_endpoint": f"{AUTH}/token",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": pkce if pkce is not None else ["S256"],
    }
    if registration:
        metadata["registration_endpoint"] = f"{AUTH}/register"
    return {
        SERVER: httpx2.Response(
            401,
            headers={
                "WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp", scope="read write"'
            },
        ),
        "https://mcp.example.com/.well-known/oauth-protected-resource/mcp": httpx2.Response(
            200,
            json={
                "resource": SERVER,
                "authorization_servers": [AUTH],
                "scopes_supported": ["read"],
            },
        ),
        f"{AUTH}/.well-known/oauth-authorization-server": httpx2.Response(200, json=metadata),
    }


async def test_endpoints_are_discovered_from_the_servers_own_challenge() -> None:
    found = await discover(SERVER, client=_client(**_full_server()))
    assert found.authorization_url == f"{AUTH}/authorize"
    assert found.token_url == f"{AUTH}/token"
    assert found.registration_url == f"{AUTH}/register"
    assert found.scopes == "read write"  # the scope the 401 asked for wins


async def test_every_fetched_url_goes_through_the_ssrf_guard(_public: list[str]) -> None:
    await discover(SERVER, client=_client(**_full_server()))
    assert SERVER in _public
    assert any(".well-known/oauth-protected-resource" in u for u in _public)
    assert any(".well-known/oauth-authorization-server" in u for u in _public)


async def test_an_authorization_server_without_pkce_is_refused() -> None:
    with pytest.raises(DiscoveryError, match="PKCE"):
        await discover(SERVER, client=_client(**_full_server(pkce=["plain"])))


async def test_a_server_with_no_oauth_metadata_says_so() -> None:
    with pytest.raises(DiscoveryError, match="does not advertise"):
        await discover(SERVER, client=_client())


async def test_a_blocked_target_stops_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    async def block(url: str) -> None:
        raise ValueError(f"{url} resolves to a non-public address")

    monkeypatch.setattr(mcp_discovery, "resolve_public_target", block)
    with pytest.raises(DiscoveryError, match="non-public"):
        await discover(SERVER, client=_client(**_full_server()))


async def test_a_client_is_registered_as_a_public_pkce_client() -> None:
    seen: list[dict[str, object]] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == f"{AUTH}/register":
            seen.append(json.loads(request.content))
            return httpx2.Response(
                201,
                json={
                    **seen[-1],
                    "client_id": "dyn-123",
                    "redirect_uris": ["https://rocket.example/v1/mcp/oauth/callback"],
                },
            )
        return _full_server().get(str(request.url), httpx2.Response(404))

    http = _client(httpx2.MockTransport(respond))
    found = await discover(SERVER, client=http)
    registered = await register_client(
        found, "https://rocket.example/v1/mcp/oauth/callback", client=http
    )

    assert registered.client_id == "dyn-123" and registered.client_secret is None
    sent = seen[0]
    assert sent["token_endpoint_auth_method"] == "none"
    assert sent["redirect_uris"] == ["https://rocket.example/v1/mcp/oauth/callback"]
    assert sent["client_name"] == "Rocket Chat"
    assert sent["grant_types"] == ["authorization_code", "refresh_token"]


async def test_a_server_that_does_not_allow_registration_asks_for_a_client_id() -> None:
    found = await discover(SERVER, client=_client(**_full_server(registration=False)))
    with pytest.raises(DiscoveryError, match="pre-registered"):
        await register_client(found, "https://rocket.example/cb")


async def test_a_registration_the_server_rejects_is_reported() -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == f"{AUTH}/register":
            return httpx2.Response(400, json={"error": "invalid_redirect_uri"})
        return _full_server().get(str(request.url), httpx2.Response(404))

    http = _client(httpx2.MockTransport(respond))
    found = await discover(SERVER, client=http)
    with pytest.raises(DiscoveryError, match="registration failed"):
        await register_client(found, "https://rocket.example/cb", client=http)
