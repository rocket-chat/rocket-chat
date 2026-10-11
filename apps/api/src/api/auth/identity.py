"""Single source of truth for resolving the caller's identity.

Every HTTP route, the auth middleware and the WebSocket endpoint resolve the caller
through :func:`resolve_identity`. Nothing else may read tenant headers or invent a
default identity.

Precedence: identity already attached by the middleware, then tenant headers (only when
a trusted proxy or the explicit development bypass is enabled), then credentials
(signed session token or cookie, OIDC bearer JWT, legacy NextAuth cookie), then the
development fallback identity (development bypass only). Anything else is anonymous.
"""

from __future__ import annotations

import logging
import os

from starlette.requests import HTTPConnection

from api.auth.context import RequestContext
from api.auth.jwt_validator import JWTValidationError, JWTValidator
from api.auth.nextauth_cookie import extract_nextauth_context
from api.auth.session_manager import extract_session_context, verify_session_token

logger = logging.getLogger(__name__)

_TRUTHY = ("true", "1", "yes")
_DEV_ROLES = ["admin", "developer"]


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def is_production() -> bool:
    return os.getenv("ENVIRONMENT", "").strip().lower() == "production"


def dev_identity_enabled() -> bool:
    """Anonymous development identity; never honoured in production."""
    return _flag("DEV_AUTH_BYPASS") and not is_production()


def trust_proxy_headers() -> bool:
    """Whether ``X-Tenant-*`` / ``X-User-Role`` headers may establish identity.

    Only safe when every request arrives through a proxy that strips and re-sets them.
    """
    return _flag("ROCKET_TRUST_PROXY_HEADERS")


def oidc_configured() -> bool:
    return bool(os.getenv("OIDC_JWKS_URL") or os.getenv("OIDC_PUBLIC_KEY"))


def _header_context(conn: HTTPConnection) -> RequestContext | None:
    if not (trust_proxy_headers() or dev_identity_enabled()):
        return None
    user_id = conn.headers.get("X-Tenant-User-Id")
    if not user_id:
        return None
    raw_role = conn.headers.get("X-User-Role") or "developer"
    roles = [role.strip() for role in raw_role.split(",") if role.strip()] or ["developer"]
    org_id = conn.headers.get("X-Tenant-Org-Id") or "default_org"
    return RequestContext(
        user_id=user_id, org_id=org_id, email=f"{user_id}@{org_id}.internal", roles=roles
    )


def _bearer_context(token: str, validator: JWTValidator | None) -> RequestContext | None:
    signed = verify_session_token(token)
    if signed is not None:
        return signed
    if validator is None and not oidc_configured():
        return None
    try:
        return (validator or JWTValidator()).validate_token(token)
    except JWTValidationError as err:
        logger.warning("Rejected bearer token: %s", err)
        return None


def _credential_context(
    conn: HTTPConnection, validator: JWTValidator | None
) -> RequestContext | None:
    auth_header = conn.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return _bearer_context(auth_header[7:].strip(), validator)
    return extract_session_context(conn) or extract_nextauth_context(conn)


def _dev_context(conn: HTTPConnection) -> RequestContext | None:
    if not dev_identity_enabled():
        return None
    org_id = conn.headers.get("X-Tenant-Org-Id") or "default_org"
    raw_role = conn.headers.get("X-User-Role")
    roles = [r.strip() for r in raw_role.split(",")] if raw_role else list(_DEV_ROLES)
    return RequestContext(
        user_id="dev_user", org_id=org_id, email="dev@rocket-chat.internal", roles=roles
    )


def resolve_identity(
    conn: HTTPConnection, validator: JWTValidator | None = None
) -> RequestContext | None:
    """Return the authenticated caller, or ``None`` when the request is anonymous."""
    attached = getattr(conn.state, "context", None)
    if isinstance(attached, RequestContext):
        return attached
    return _header_context(conn) or _credential_context(conn, validator) or _dev_context(conn)


def resolve_token_identity(token: str) -> RequestContext | None:
    """Identity for a credential passed outside headers (WebSocket ``?token=``)."""
    return _bearer_context(token, None) if token else None
