"""Signed read-only links to one session.

A public link is a capability: whoever holds the URL can read the session's conversation, so it
names exactly one session and one link id, expires, and can be revoked. Revoking clears the id
stored on the session, which makes every token minted for it fail verification at once.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt

from .session_manager import get_session_secret

MAX_LINK_DAYS = 30
DEFAULT_LINK_DAYS = 7
_TYPE = "share"


@dataclass(frozen=True)
class ShareGrant:
    session_id: str
    org_id: str
    link_id: str
    expires_at: int


def mint(session_id: str, org_id: str, link_id: str, days: int) -> tuple[str, int]:
    now = int(time.time())
    expires_at = now + max(1, min(days, MAX_LINK_DAYS)) * 86400
    token = jwt.encode(
        {
            "typ": _TYPE,
            "sid": session_id,
            "org": org_id,
            "lid": link_id,
            "iat": now,
            "exp": expires_at,
        },
        get_session_secret(),
        algorithm="HS256",
    )
    return token, expires_at


def verify(token: str) -> ShareGrant:
    """The grant in a genuine, unexpired share token, or ``ValueError``."""
    try:
        claims = jwt.decode(token, get_session_secret(), algorithms=["HS256"])
    except jwt.InvalidTokenError as err:
        raise ValueError("This link is invalid or has expired") from err
    if claims.get("typ") != _TYPE:
        raise ValueError("This link is invalid or has expired")
    return ShareGrant(
        session_id=str(claims["sid"]),
        org_id=str(claims["org"]),
        link_id=str(claims["lid"]),
        expires_at=int(claims["exp"]),
    )
