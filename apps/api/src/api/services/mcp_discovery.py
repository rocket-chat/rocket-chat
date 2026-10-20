"""OAuth discovery and dynamic client registration for MCP servers.

A remote MCP server that wants OAuth advertises it: a ``401`` with a ``WWW-Authenticate``
pointer to its protected-resource metadata (RFC 9728), which names an authorization server whose
metadata (RFC 8414) gives the authorize/token/registration endpoints; a client with no
pre-shared ``client_id`` registers itself (RFC 7591). The MCP SDK implements the URL rules and
response parsing; this module only performs the fetches - through the same SSRF guard as every
other member-influenced URL, once per hop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx2
from agent_core.tools.safe_http import resolve_public_target
from mcp.client.auth import utils as sdk
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata, ProtectedResourceMetadata
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url
from pydantic import AnyUrl

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
CLIENT_NAME = "Rocket Chat"


class DiscoveryError(RuntimeError):
    """The server does not offer OAuth this client can use."""


@dataclass(frozen=True)
class Discovered:
    authorization_url: str
    token_url: str
    registration_url: str | None
    scopes: str | None
    issuer: str | None
    pkce_s256: bool
    # The canonical server URI tokens are requested for (RFC 8707 ``resource``).
    resource: str | None = None
    # The authorization server accepts a client ID that is a URL to the client's metadata.
    client_metadata_supported: bool = False


@dataclass(frozen=True)
class Registered:
    client_id: str
    client_secret: str | None
    # How the client was obtained: "registered" (RFC 7591) or "metadata_url" (a URL client ID).
    method: str = "registered"


def _resource_for(server_url: str, resource: ProtectedResourceMetadata | None) -> str:
    """The server's own resource identifier when it covers this URL, else its canonical URI."""
    canonical = resource_url_from_server_url(server_url)
    advertised = str(resource.resource) if resource is not None and resource.resource else ""
    if advertised and check_resource_allowed(canonical, advertised):
        return advertised
    return canonical


async def _send(client: httpx2.AsyncClient, request: httpx2.Request) -> httpx2.Response:
    await resolve_public_target(str(request.url))
    return await client.send(request)


async def discover(server_url: str, *, client: httpx2.AsyncClient | None = None) -> Discovered:
    """Find the authorization server for ``server_url`` and its endpoints."""
    owned = client is None
    http = client or httpx2.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False)
    try:
        challenge = await _send(http, http.build_request("GET", server_url))
        www_authenticate_url = sdk.extract_resource_metadata_from_www_auth(challenge)
        advertised_scope = sdk.extract_scope_from_www_auth(challenge)

        resource: ProtectedResourceMetadata | None = None
        for url in sdk.build_protected_resource_metadata_discovery_urls(
            www_authenticate_url, server_url
        ):
            response = await _send(http, sdk.create_oauth_metadata_request(url))
            resource = await sdk.handle_protected_resource_response(response)
            if resource is not None:
                break

        auth_server = (
            str(resource.authorization_servers[0])
            if resource and resource.authorization_servers
            else None
        )
        metadata: OAuthMetadata | None = None
        for url in sdk.build_oauth_authorization_server_metadata_discovery_urls(
            auth_server, server_url
        ):
            response = await _send(http, sdk.create_oauth_metadata_request(url))
            ok, metadata = await sdk.handle_auth_metadata_response(response)
            if metadata is not None:
                break
            if not ok:
                break
        if metadata is None or not metadata.authorization_endpoint or not metadata.token_endpoint:
            raise DiscoveryError("The server does not advertise an OAuth authorization server.")
        if metadata.code_challenge_methods_supported is not None and (
            "S256" not in metadata.code_challenge_methods_supported
        ):
            raise DiscoveryError("The authorization server does not support PKCE (S256).")
        return Discovered(
            authorization_url=str(metadata.authorization_endpoint),
            token_url=str(metadata.token_endpoint),
            registration_url=(
                str(metadata.registration_endpoint) if metadata.registration_endpoint else None
            ),
            scopes=sdk.get_client_metadata_scopes(advertised_scope, resource, metadata),
            issuer=str(metadata.issuer) if metadata.issuer else None,
            pkce_s256=True,
            resource=_resource_for(server_url, resource),
            client_metadata_supported=metadata.client_id_metadata_document_supported is True,
        )
    except DiscoveryError:
        raise
    except Exception as err:
        raise DiscoveryError(f"OAuth discovery failed: {err}") from err
    finally:
        if owned:
            await http.aclose()


async def register_client(
    discovered: Discovered,
    redirect_uri: str,
    *,
    scope: str | None = None,
    client: httpx2.AsyncClient | None = None,
) -> Registered:
    """Dynamically register a public (PKCE) client; the server decides the ``client_id``."""
    if not discovered.registration_url:
        raise DiscoveryError(
            "This server needs a pre-registered client: enter a client ID (and secret) for it."
        )
    owned = client is None
    http = client or httpx2.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False)
    try:
        metadata = OAuthClientMetadata(
            redirect_uris=[AnyUrl(redirect_uri)],
            client_name=CLIENT_NAME,
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=scope or discovered.scopes,
        )
        request = sdk.create_client_registration_request(
            OAuthMetadata.model_validate(
                {
                    "issuer": discovered.issuer or discovered.authorization_url,
                    "authorization_endpoint": discovered.authorization_url,
                    "token_endpoint": discovered.token_url,
                    "registration_endpoint": discovered.registration_url,
                }
            ),
            metadata,
            discovered.registration_url,
        )
        response = await _send(http, request)
        info = await sdk.handle_registration_response(response)
    except DiscoveryError:
        raise
    except Exception as err:
        raise DiscoveryError(f"Client registration failed: {err}") from err
    finally:
        if owned:
            await http.aclose()
    if not info.client_id:
        raise DiscoveryError("The server did not return a client ID.")
    return Registered(client_id=info.client_id, client_secret=info.client_secret)


async def establish_client(
    discovered: Discovered,
    redirect_uri: str,
    *,
    client_metadata_url: str | None = None,
    client: httpx2.AsyncClient | None = None,
) -> Registered:
    """A client ID for this authorization server, with nothing set up on the provider's side.

    Registration (RFC 7591) comes first: the authorization server answers it directly, while a
    URL client ID only works if the server can fetch our metadata document, which a deployment
    on a private network does not expose. Without either, the provider needs an OAuth app
    created by hand, and the person is told so instead of being sent to a login that fails.
    """
    if discovered.registration_url:
        return await register_client(discovered, redirect_uri, client=client)
    if (
        discovered.client_metadata_supported
        and client_metadata_url
        and sdk.is_valid_client_metadata_url(client_metadata_url)
    ):
        return Registered(client_id=client_metadata_url, client_secret=None, method="metadata_url")
    raise DiscoveryError(
        "This provider does not let applications register themselves: create an OAuth app "
        f"there with the callback URL {redirect_uri} and enter its client ID (and secret)."
    )
