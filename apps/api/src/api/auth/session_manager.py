"""Unified backend session manager handling signed session cookies and tokens."""

import logging
import os
import time
from typing import Any

import jwt
from fastapi import Response
from starlette.requests import HTTPConnection

from .context import RequestContext

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "rocket_session"
DEFAULT_SESSION_SECRET = "rocket-chat-development-session-secret-key-32-chars-min"
SESSION_DURATION_SECONDS = 30 * 24 * 60 * 60  # 30 days


def get_session_secret() -> str:
    """Resolve session secret key from environment configuration."""
    secret = os.getenv("SESSION_SECRET") or os.getenv("NEXTAUTH_SECRET")
    if secret:
        return secret
    if os.getenv("ENVIRONMENT", "").strip().lower() == "production":
        raise RuntimeError("SESSION_SECRET is required in production")
    return DEFAULT_SESSION_SECRET


def create_session_token(
    user_id: str,
    org_id: str = "default_org",
    email: str | None = None,
    name: str | None = None,
    roles: list[str] | None = None,
    groups: list[str] | None = None,
    expires_in: int = SESSION_DURATION_SECONDS,
) -> str:
    """Create a signed HS256 JWT session token."""
    now = int(time.time())
    user_email = email or f"{user_id}@{org_id}.internal"
    user_roles = roles if roles is not None else ["developer"]
    user_groups = groups if groups is not None else []

    payload: dict[str, Any] = {
        "sub": user_id,
        "user_id": user_id,
        "org_id": org_id,
        "email": user_email,
        "name": name or user_id,
        "roles": user_roles,
        "groups": user_groups,
        "iat": now,
        "exp": now + expires_in,
    }

    secret = get_session_secret()
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_session_token(token: str) -> RequestContext | None:
    """Verify signed session token and return RequestContext."""
    secret = get_session_secret()
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
        user_id = claims.get("sub") or claims.get("user_id")
        if not user_id:
            return None

        org_id = claims.get("org_id") or "default_org"
        email = claims.get("email") or f"{user_id}@{org_id}.internal"
        name = claims.get("name")
        roles = claims.get("roles") or ["developer"]
        if not isinstance(roles, list):
            roles = [str(roles)]
        groups = claims.get("groups") or []
        if not isinstance(groups, list):
            groups = [str(groups)]

        return RequestContext(
            user_id=str(user_id),
            org_id=str(org_id),
            email=str(email),
            name=name,
            roles=roles,
            groups=groups,
            source="session",
        )
    except jwt.ExpiredSignatureError:
        logger.debug("Session token expired")
        return None
    except jwt.InvalidTokenError as err:
        logger.debug("Invalid session token: %s", err)
        return None
    except Exception as err:
        logger.debug("Error verifying session token: %s", err)
        return None


def extract_session_context(request: HTTPConnection) -> RequestContext | None:
    """Extract and verify session token from HTTP-only cookie or Authorization header."""
    # 1. Check Authorization Bearer header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        ctx = verify_session_token(token)
        if ctx:
            return ctx

    # 2. Check unified rocket_session cookie
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        ctx = verify_session_token(cookie_token)
        if ctx:
            return ctx

    return None


def set_session_cookie(
    response: Response,
    token: str,
    max_age: int = SESSION_DURATION_SECONDS,
) -> None:
    """Attach secure, HTTP-only session cookie to HTTP response."""
    is_prod = (
        os.getenv("ENVIRONMENT", "").lower() == "production"
        or os.getenv("NODE_ENV", "") == "production"
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        expires=max_age,
        path="/",
        httponly=True,
        secure=is_prod,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    """Delete session cookie on logout."""
    is_prod = (
        os.getenv("ENVIRONMENT", "").lower() == "production"
        or os.getenv("NODE_ENV", "") == "production"
    )
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=is_prod,
        samesite="lax",
    )
