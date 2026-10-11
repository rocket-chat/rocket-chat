"""Short-lived capability tokens for the app preview.

A preview is rendered in a sandboxed iframe with an opaque origin, which sends no cookies, so
its requests cannot be authenticated like the cockpit's. The authority travels in the URL
instead: a signed token naming exactly one session and port, valid for an hour. It grants a
view of what that sandbox serves on that port and nothing else.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt

from .session_manager import get_session_secret

PREVIEW_TTL_SECONDS = 3600
_TYPE = "preview"


@dataclass(frozen=True)
class PreviewGrant:
    session_id: str
    org_id: str
    user_id: str
    port: int
    expires_at: int


def mint(session_id: str, org_id: str, user_id: str, port: int) -> tuple[str, int]:
    now = int(time.time())
    expires_at = now + PREVIEW_TTL_SECONDS
    token = jwt.encode(
        {
            "typ": _TYPE,
            "sid": session_id,
            "org": org_id,
            "uid": user_id,
            "port": port,
            "iat": now,
            "exp": expires_at,
        },
        get_session_secret(),
        algorithm="HS256",
    )
    return token, expires_at


def verify(token: str) -> PreviewGrant:
    """The grant in a genuine, unexpired preview token, or ``ValueError``."""
    try:
        claims = jwt.decode(token, get_session_secret(), algorithms=["HS256"])
    except jwt.InvalidTokenError as err:
        raise ValueError("Invalid or expired preview link") from err
    if claims.get("typ") != _TYPE:
        raise ValueError("Not a preview token")
    return PreviewGrant(
        session_id=str(claims["sid"]),
        org_id=str(claims["org"]),
        user_id=str(claims["uid"]),
        port=int(claims["port"]),
        expires_at=int(claims["exp"]),
    )
