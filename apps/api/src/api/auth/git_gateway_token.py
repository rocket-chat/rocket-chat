"""The sandbox's capability for the git gateway.

A sandbox holds no GitHub credential. It clones and fetches through the platform's git gateway
(``/_git/...``), which adds the member's or the GitHub App's credential on the platform side.
The sandbox only holds this token: signed, naming one session, valid for an hour, minted again
whenever less than ``RENEW_BEFORE_SECONDS`` is left (see ``services/git_credentials.py``), and
accepted by nothing but the gateway, which only serves reads.

A turn can run for hours and a process it started (a build, a watcher) keeps the environment it
was born with, so renewal alone cannot cover every holder of an old token. The gateway therefore
also accepts a token that expired up to ``EXPIRED_GRACE_SECONDS`` ago while its session still has
a turn running (``GitGateway.authorize``): the signature, the session and the read-only scope
are what the token proves, and the clock only bounds a session nobody is working in.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt

from .session_manager import get_session_secret

GIT_GATEWAY_TTL_SECONDS = 3600
# A token with less than this left is replaced before the next command.
RENEW_BEFORE_SECONDS = 20 * 60
# How long after its expiry a token still works for a session with a running turn.
EXPIRED_GRACE_SECONDS = 24 * 3600
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


class GatewayCredentialExpired(ValueError):
    """A genuine token past its expiry; ``grant`` is what it names, for a liveness check."""

    def __init__(self, grant: GitGatewayGrant) -> None:
        super().__init__("Invalid or expired git gateway credential")
        self.grant = grant


def _grant_of(claims: dict[str, object]) -> GitGatewayGrant:
    if claims.get("typ") != _TYPE:
        raise ValueError("Not a git gateway credential")
    return GitGatewayGrant(
        session_id=str(claims["sid"]),
        org_id=str(claims["org"]),
        user_id=str(claims["uid"]),
        expires_at=int(str(claims["exp"])),
    )


def verify(token: str, *, now: float | None = None) -> GitGatewayGrant:
    """The grant in a genuine, unexpired gateway token, or ``ValueError``.

    A genuine token that expired less than ``EXPIRED_GRACE_SECONDS`` ago raises
    ``GatewayCredentialExpired``, so the gateway can still serve it to a session that is working.
    """
    secret = get_session_secret()
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_exp": False})
    except jwt.InvalidTokenError as err:
        raise ValueError("Invalid or expired git gateway credential") from err
    grant = _grant_of(claims)
    current = now if now is not None else time.time()
    if grant.expires_at > current:
        return grant
    if current - grant.expires_at <= EXPIRED_GRACE_SECONDS:
        raise GatewayCredentialExpired(grant)
    raise ValueError("Invalid or expired git gateway credential")
