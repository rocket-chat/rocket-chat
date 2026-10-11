"""Generic OpenID Connect login through provider discovery.

Works with any standards-compliant provider (Okta, Azure AD / Entra, Google, Keycloak,
Authentik, ...): endpoints come from ``/.well-known/openid-configuration`` rather than being
hard-coded to one vendor's URL scheme. The authorization-code flow uses PKCE and a nonce, and
the ID token is verified against the provider's JWKS (signature, issuer, audience, expiry,
nonce) before anything in it is trusted.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

logger = logging.getLogger(__name__)

METADATA_TTL_SECONDS = 3600.0
HTTP_TIMEOUT_SECONDS = 10.0
CLOCK_LEEWAY_SECONDS = 60
# Asymmetric algorithms only: accepting HS* would let anyone who knows the (public) client id
# forge tokens signed with the client secret as an HMAC key.
SIGNING_ALGORITHMS = [
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
    "EdDSA",
]


class OidcError(Exception):
    """Login failed; the message is safe to show to the user."""


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    scopes: str = "openid email profile"
    discovery_url: str = ""

    @classmethod
    def from_env(cls) -> OidcConfig | None:
        issuer = (os.getenv("OIDC_ISSUER") or "").rstrip("/")
        client_id = os.getenv("OIDC_CLIENT_ID") or ""
        if not issuer or not client_id:
            return None
        return cls(
            issuer=issuer,
            client_id=client_id,
            client_secret=os.getenv("OIDC_CLIENT_SECRET") or "",
            scopes=os.getenv("OIDC_SCOPES") or "openid email profile",
            discovery_url=os.getenv("OIDC_DISCOVERY_URL") or "",
        )

    @property
    def metadata_url(self) -> str:
        return self.discovery_url or f"{self.issuer}/.well-known/openid-configuration"


@dataclass(frozen=True)
class AuthorizationRequest:
    """Everything the callback needs to finish a login that this redirect started."""

    url: str
    code_verifier: str
    nonce: str


@dataclass(frozen=True)
class OidcIdentity:
    subject: str
    email: str
    email_verified: bool | None
    name: str
    claims: dict[str, Any] = field(repr=False, default_factory=dict)


@dataclass
class _Cached:
    value: dict[str, Any]
    fetched_at: float


_metadata_cache: dict[str, _Cached] = {}
_jwks_cache: dict[str, _Cached] = {}


# Tests route provider traffic to an in-process fixture; production leaves this unset.
_transport_override: httpx.AsyncBaseTransport | None = None
# Authlib's client may be built on a different httpx generation than ours (it prefers ``httpx2``
# when installed), whose transports are not interchangeable.
_oauth_transport_override: Any = None


def _transport(explicit: httpx.AsyncBaseTransport | None) -> httpx.AsyncBaseTransport | None:
    return explicit or _transport_override


def reset_caches() -> None:
    _metadata_cache.clear()
    _jwks_cache.clear()


def pkce_pair() -> tuple[str, str]:
    """A PKCE ``(code_verifier, S256 code_challenge)`` pair."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    response = await client.get(url, headers={"Accept": "application/json"})
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise OidcError(f"Unexpected response from {url}")
    return data


async def discover(
    config: OidcConfig, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    """The provider's discovery document (cached), checked against the configured issuer."""
    cached = _metadata_cache.get(config.metadata_url)
    if cached and time.monotonic() - cached.fetched_at < METADATA_TTL_SECONDS:
        return cached.value
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS, transport=_transport(transport)
    ) as client:
        try:
            metadata = await _get_json(client, config.metadata_url)
        except (httpx.HTTPError, ValueError) as err:
            raise OidcError(f"OIDC discovery failed for {config.issuer}") from err
    # RFC 8414 section 3.3: the document must name the issuer we asked about, otherwise a
    # compromised discovery endpoint could redirect logins to another provider.
    if str(metadata.get("issuer", "")).rstrip("/") != config.issuer:
        raise OidcError("OIDC discovery document does not match the configured issuer")
    for required in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not metadata.get(required):
            raise OidcError(f"OIDC discovery document lacks '{required}'")
    _metadata_cache[config.metadata_url] = _Cached(metadata, time.monotonic())
    return metadata


async def _load_jwks(
    jwks_uri: str, *, transport: httpx.AsyncBaseTransport | None, refresh: bool = False
) -> KeySet:
    cached = _jwks_cache.get(jwks_uri)
    if cached and not refresh and time.monotonic() - cached.fetched_at < METADATA_TTL_SECONDS:
        return KeySet.import_key_set(cached.value)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS, transport=_transport(transport)
    ) as client:
        try:
            document = await _get_json(client, jwks_uri)
        except (httpx.HTTPError, ValueError) as err:
            raise OidcError("Could not fetch the provider's signing keys") from err
    _jwks_cache[jwks_uri] = _Cached(document, time.monotonic())
    return KeySet.import_key_set(document)  # type: ignore[arg-type]


async def begin_login(
    config: OidcConfig,
    *,
    redirect_uri: str,
    state: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AuthorizationRequest:
    """The provider URL to send the browser to, plus the secrets to keep for the callback."""
    metadata = await discover(config, transport=transport)
    verifier, challenge = pkce_pair()
    nonce = secrets.token_urlsafe(24)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": redirect_uri,
            "scope": config.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    separator = "&" if "?" in metadata["authorization_endpoint"] else "?"
    return AuthorizationRequest(
        url=f"{metadata['authorization_endpoint']}{separator}{query}",
        code_verifier=verifier,
        nonce=nonce,
    )


def _audience_ok(claims: dict[str, Any], client_id: str) -> bool:
    audience = claims.get("aud")
    values = [audience] if isinstance(audience, str) else list(audience or [])
    if client_id not in values:
        return False
    # OIDC Core 3.1.3.7: with several audiences, `azp` must name this client.
    return len(values) == 1 or claims.get("azp") == client_id


async def _verify_id_token(
    config: OidcConfig,
    metadata: dict[str, Any],
    id_token: str,
    nonce: str,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    async def decode(refresh: bool) -> jwt.Token:
        keys = await _load_jwks(metadata["jwks_uri"], transport=transport, refresh=refresh)
        return jwt.decode(id_token, keys, algorithms=SIGNING_ALGORITHMS)

    try:
        try:
            token = await decode(refresh=False)
        except JoseError:
            # Providers rotate keys; one refetch distinguishes rotation from a forged token.
            token = await decode(refresh=True)
        registry = jwt.JWTClaimsRegistry(
            leeway=CLOCK_LEEWAY_SECONDS,
            iss={"essential": True, "value": config.issuer},
            exp={"essential": True},
            sub={"essential": True},
            nonce={"essential": True, "value": nonce},
        )
        registry.validate(token.claims)
    except JoseError as err:
        raise OidcError("The provider's identity token could not be verified") from err
    if not _audience_ok(token.claims, config.client_id):
        raise OidcError("The identity token was issued for a different application")
    return dict(token.claims)


async def complete_login(
    config: OidcConfig,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    nonce: str,
    transport: Any = None,
) -> OidcIdentity:
    """Exchange the authorization code, verify the ID token and return who logged in."""
    metadata = await discover(config, transport=transport)
    supported = metadata.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    auth_method = (
        "client_secret_post"
        if "client_secret_post" in supported and ("client_secret_basic" not in supported)
        else "client_secret_basic"
    )
    async with AsyncOAuth2Client(
        client_id=config.client_id,
        client_secret=config.client_secret or None,
        token_endpoint_auth_method=auth_method if config.client_secret else "none",
        timeout=HTTP_TIMEOUT_SECONDS,
        transport=transport or _oauth_transport_override,
    ) as client:
        try:
            token = await client.fetch_token(
                metadata["token_endpoint"],
                grant_type="authorization_code",
                code=code,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
            )
        except Exception as err:
            logger.warning("OIDC token exchange failed: %s", type(err).__name__)
            raise OidcError("The provider rejected the login (token exchange failed)") from err

        if not token.get("access_token"):
            # An error body (invalid_grant, invalid_client, bad PKCE verifier) is not a token.
            raise OidcError("The provider rejected the login (token exchange failed)")
        id_token = token.get("id_token")
        if not id_token:
            raise OidcError("The provider did not return an identity token")
        claims = await _verify_id_token(config, metadata, id_token, nonce, transport)

        email = claims.get("email")
        name = claims.get("name") or claims.get("preferred_username")
        if (
            (not email or not name)
            and metadata.get("userinfo_endpoint")
            and token.get("access_token")
        ):
            # Some providers keep profile claims out of the ID token. The subject must agree so
            # the profile cannot belong to someone else.
            profile = await _fetch_userinfo(
                client, metadata["userinfo_endpoint"], token["access_token"]
            )
            if profile.get("sub") == claims["sub"]:
                email = email or profile.get("email")
                name = name or profile.get("name") or profile.get("preferred_username")
                claims = {**profile, **claims}

    email_text = str(email or "").lower()
    return OidcIdentity(
        subject=str(claims["sub"]),
        email=email_text,
        email_verified=claims.get("email_verified"),
        name=str(name or email_text.split("@")[0]),
        claims=claims,
    )


async def _fetch_userinfo(client: AsyncOAuth2Client, url: str, access_token: str) -> dict[str, Any]:
    try:
        response = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
        return response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return {}
