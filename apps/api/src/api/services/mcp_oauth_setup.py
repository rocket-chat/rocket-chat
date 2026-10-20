"""Settling how Rocket Chat signs in to an MCP server: endpoints, resource and client.

With a server that describes itself (RFC 9728/8414) nothing is configured on either side: the
endpoints are discovered and the client is registered (RFC 7591) or named by our metadata URL.
A client is tied to the callback URL it was set up with, so it is set up again when that URL
changes (a client registered with the backend's internal address could never complete a login).
"""

from __future__ import annotations

from typing import Any

from config_engine.crypto import CredentialCipher

from api.services.mcp_discovery import DiscoveryError, discover, establish_client

CLIENT_METADATA_PATH = "/v1/mcp/oauth/client-metadata.json"
CLIENT_NAME = "Rocket Chat"
# A client someone created on the provider and entered by hand: never replaced.
MANUAL_CLIENT = "manual"


def client_metadata_url(origin: str) -> str:
    return f"{origin}{CLIENT_METADATA_PATH}"


def client_metadata_document(origin: str, redirect_uri: str) -> dict[str, Any]:
    """What an authorization server reads when our client ID is the URL of this document."""
    return {
        "client_id": client_metadata_url(origin),
        "client_name": CLIENT_NAME,
        "client_uri": origin,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


def needs_settling(auth: dict[str, Any], redirect_uri: str) -> bool:
    """Whether the server's endpoints or client must be (re)discovered before a login."""
    if not (
        auth.get("oauth_authorization_url")
        and auth.get("oauth_token_url")
        and auth.get("oauth_client_id")
    ):
        return True
    if auth.get("oauth_client_method") == MANUAL_CLIENT:
        return False
    # A client set up automatically for another callback URL, or before that URL was recorded.
    return auth.get("oauth_registered_redirect_uri") != redirect_uri


async def settle_oauth(
    auth: dict[str, Any],
    endpoint: str,
    redirect_uri: str,
    origin: str,
    cipher: CredentialCipher,
) -> dict[str, Any]:
    """``auth`` with the server's endpoints, resource and a client for ``redirect_uri``.

    Raises :class:`DiscoveryError` when the server cannot be signed in to automatically.
    """
    if not endpoint.startswith(("http://", "https://")):
        raise DiscoveryError("Only an HTTP MCP server can be signed in to with OAuth.")
    found = await discover(endpoint)
    settled = dict(auth)
    settled["oauth_authorization_url"] = found.authorization_url
    settled["oauth_token_url"] = found.token_url
    if found.resource:
        settled["oauth_resource"] = found.resource
    if not settled.get("oauth_scopes"):
        settled["oauth_scopes"] = found.scopes or ""
    if settled.get("oauth_client_method") != MANUAL_CLIENT:
        client = await establish_client(
            found, redirect_uri, client_metadata_url=client_metadata_url(origin)
        )
        settled["oauth_client_id"] = client.client_id
        if client.client_secret:
            settled["oauth_client_secret"] = cipher.encrypt(client.client_secret)
        else:
            settled.pop("oauth_client_secret", None)
        settled["oauth_client_method"] = client.method
        settled["oauth_registered_redirect_uri"] = redirect_uri
    return settled
