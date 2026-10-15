"""The sandbox's capability for the git gateway.

A sandbox holds no GitHub credential. It clones and fetches through the platform's git gateway
(``/_git/...``), which adds the member's or the GitHub App's credential on the platform side.
The sandbox only holds this token: signed, naming one session, valid for an hour, minted again at
every turn, and accepted by nothing but the gateway, which only serves reads.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt

from .session_manager import get_session_secret

GIT_GATEWAY_TTL_SECONDS = 3600
_TYPE = "git_gateway"


@dataclass(frozen=True)
class GitGatewayGrant:
    session_id: str
    org_id: str
    user_id: str
    expires_at: int


def mint(session_id: str, org_id: str, user_id: str, *, now: float | None = None) -> str:
    issued = int(now if now is not None else time.time())
    return jwt.encode(
        {
            "typ": _TYPE,
            "sid": session_id,
            "org": org_id,
            "uid": user_id,
            "iat": issued,
            "exp": issued + GIT_GATEWAY_TTL_SECONDS,
        },
        get_session_secret(),
        algorithm="HS256",
    )


def verify(token: str) -> GitGatewayGrant:
    """The grant in a genuine, unexpired gateway token, or ``ValueError``."""
    try:
        claims = jwt.decode(token, get_session_secret(), algorithms=["HS256"])
    except jwt.InvalidTokenError as err:
        raise ValueError("Invalid or expired git gateway credential") from err
    if claims.get("typ") != _TYPE:
        raise ValueError("Not a git gateway credential")
    return GitGatewayGrant(
        session_id=str(claims["sid"]),
        org_id=str(claims["org"]),
        user_id=str(claims["uid"]),
        expires_at=int(claims["exp"]),
    )
