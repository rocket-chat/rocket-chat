"""Signed, expiring OAuth state and flow secrets.

The ``state`` URL parameter travels through the provider and the user's address bar, so it is
signed (a forged ``link_only``/``user_id`` is rejected) but must never carry secrets. The PKCE
verifier and nonce live in a second signed token held in an HttpOnly cookie that only this
browser sends back, bound to the state it belongs to.
"""

from __future__ import annotations

import time
from typing import Any

import jwt

from .session_manager import get_session_secret

STATE_TTL_SECONDS = 600
_TYPE = "oauth_state"


def sign(payload: dict[str, Any]) -> str:
    now = int(time.time())
    return jwt.encode(
        {**payload, "typ": _TYPE, "iat": now, "exp": now + STATE_TTL_SECONDS},
        get_session_secret(),
        algorithm="HS256",
    )


def signed_purpose(token: str) -> str | None:
    """The ``purpose`` of a genuine state token even once expired, else ``None``.

    Tells which flow a returning browser belongs to, so an expired link is reported as
    expired by its own flow instead of being mistaken for another one.
    """
    try:
        claims = jwt.decode(
            token, get_session_secret(), algorithms=["HS256"], options={"verify_exp": False}
        )
    except jwt.InvalidTokenError:
        return None
    return str(claims.get("purpose")) if claims.get("typ") == _TYPE else None


def verify(token: str) -> dict[str, Any]:
    """The payload of a genuine, unexpired state token, or ``ValueError``."""
    try:
        claims = jwt.decode(token, get_session_secret(), algorithms=["HS256"])
    except jwt.InvalidTokenError as err:
        raise ValueError("Invalid or expired OAuth state") from err
    if claims.get("typ") != _TYPE:
        raise ValueError("Not an OAuth state token")
    return dict(claims)
