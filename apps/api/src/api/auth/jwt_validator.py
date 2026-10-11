"""JWT verification utility validating RS256 tokens and extracting tenant/user claims."""

import logging
import os
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

from .context import RequestContext

logger = logging.getLogger(__name__)


# Asymmetric only: an HS* algorithm would let anyone holding the public client id forge tokens.
ACCEPTED_ALGORITHMS = [
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
]
DISCOVERY_TIMEOUT_SECONDS = 10.0

_discovered_jwks_uris: dict[str, str] = {}


def discover_jwks_uri(issuer: str) -> str:
    """The issuer's ``jwks_uri`` from ``/.well-known/openid-configuration`` (cached)."""
    issuer = issuer.rstrip("/")
    cached = _discovered_jwks_uris.get(issuer)
    if cached:
        return cached
    try:
        response = httpx.get(
            f"{issuer}/.well-known/openid-configuration",
            timeout=DISCOVERY_TIMEOUT_SECONDS,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError) as err:
        raise JWTValidationError(f"OIDC discovery failed for {issuer}: {err}") from err
    if str(document.get("issuer", "")).rstrip("/") != issuer or not document.get("jwks_uri"):
        raise JWTValidationError("OIDC discovery document does not match the configured issuer")
    _discovered_jwks_uris[issuer] = str(document["jwks_uri"])
    return _discovered_jwks_uris[issuer]


class JWTValidationError(Exception):
    """Raised when incoming JWT signature, expiration, or claims fail validation."""


class JWTValidator:
    """Validates RS256 Bearer JWTs against JWKS endpoint or configured public key."""

    def __init__(
        self,
        jwks_url: str | None = None,
        public_key: str | None = None,
        issuer: str | None = None,
        audience: str | None = None,
    ) -> None:
        self.jwks_url = jwks_url or os.getenv("OIDC_JWKS_URL")
        self.public_key = public_key or os.getenv("OIDC_PUBLIC_KEY")
        self.issuer = issuer or os.getenv("OIDC_ISSUER")
        self.audience = audience or os.getenv("OIDC_AUDIENCE")

        self._jwks_client: PyJWKClient | None = None
        if self.jwks_url:
            self._jwks_client = PyJWKClient(self.jwks_url)

    def _resolve_signing_key(self, token: str) -> Any:
        """Resolve public signing key from explicit configuration or remote JWKS."""
        if self.public_key:
            return self.public_key

        if self._jwks_client is None and self.issuer:
            # Only an issuer is configured: find its keys through OIDC discovery.
            self._jwks_client = PyJWKClient(discover_jwks_uri(self.issuer))

        if self._jwks_client:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            return signing_key.key

        raise JWTValidationError("No public key or JWKS URL configured for token verification")

    def validate_token(self, token: str) -> RequestContext:
        """Validate token signature, expiration, and issuer; extract RequestContext."""
        try:
            key = self._resolve_signing_key(token)
            options: Any = {
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": bool(self.audience),
                "verify_iss": bool(self.issuer),
            }
            claims = jwt.decode(
                token,
                key=key,
                algorithms=ACCEPTED_ALGORITHMS,
                audience=self.audience if self.audience else None,
                issuer=self.issuer if self.issuer else None,
                options=options,
            )
            return self._claims_to_context(claims)
        except jwt.ExpiredSignatureError as err:
            raise JWTValidationError("Token signature has expired") from err
        except jwt.InvalidTokenError as err:
            raise JWTValidationError(f"Invalid token: {err}") from err
        except Exception as err:
            raise JWTValidationError(f"Token validation failed: {err}") from err

    def _claims_to_context(self, claims: dict[str, Any]) -> RequestContext:
        """Extract multi-tenant identity attributes from decoded JWT claims."""
        user_id = claims.get("sub") or claims.get("user_id") or claims.get("uid")
        if not user_id:
            raise JWTValidationError("JWT missing 'sub' claim for user identity")

        org_id = (
            claims.get("org_id")
            or claims.get("tenant_org_id")
            or claims.get("custom:org_id")
            or claims.get("organization")
        )
        if not org_id:
            raise JWTValidationError("JWT missing 'org_id' claim for tenant organization")

        email = claims.get("email") or f"{user_id}@{org_id}.internal"
        team_id = claims.get("team_id") or claims.get("custom:team_id")

        # Map groups
        raw_groups = claims.get("groups") or claims.get("cognito:groups") or []
        groups = [str(g) for g in raw_groups] if isinstance(raw_groups, list) else []

        if not team_id and groups:
            # Map primary group to team
            team_id = groups[0]

        roles = claims.get("roles") or ["developer"]
        if not isinstance(roles, list):
            roles = [str(roles)]

        return RequestContext(
            user_id=str(user_id),
            org_id=str(org_id),
            team_id=str(team_id) if team_id else None,
            email=str(email),
            name=claims.get("name"),
            roles=roles,
            groups=groups,
        )
