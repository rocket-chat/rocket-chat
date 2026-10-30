"""Role names and live role resolution for session-cookie identities.

A member holds one role in an organization: ``owner``, ``admin``, ``member`` or ``viewer``.
``guest`` is internal: guest API tokens and Slack guests, confined elsewhere. Older names are
read as their successors (``developer`` -> ``member``, ``readonly`` and a human ``guest`` ->
``viewer``), so a token minted before the rename keeps working with the same rights.

A signed session token carries the roles it was minted with and lives for days. Trusting
it alone means a demoted or removed admin keeps admin rights until the token expires, so
the database role is re-read (with a short cache) and overrides the token's claim.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any

from fastapi import HTTPException

from api.auth.context import RequestContext

logger = logging.getLogger(__name__)

ROLE_CACHE_TTL_SECONDS = 15.0
# "org\0user" -> (expiry, (role in that org or "", org_id), or None when the person is unknown)
_cache: dict[str, tuple[float, tuple[str, str] | None]] = {}

OWNER, ADMIN, MEMBER, VIEWER, GUEST = "owner", "admin", "member", "viewer", "guest"
ROLES: tuple[str, ...] = (OWNER, ADMIN, MEMBER, VIEWER)

_LEGACY_ROLES = {"developer": MEMBER, "readonly": VIEWER, "guest": VIEWER}

# What each stored role grants: higher roles include the ones below that can write.
_EXPANSION: dict[str, list[str]] = {
    OWNER: [OWNER, ADMIN, MEMBER],
    ADMIN: [ADMIN, MEMBER],
    MEMBER: [MEMBER],
    VIEWER: [VIEWER],
}


def canonical_role(role: str) -> str:
    """The current name of a stored or claimed role; an unknown one is held to ``viewer``."""
    name = (role or "").strip().lower()
    if name in _EXPANSION:
        return name
    return _LEGACY_ROLES.get(name, VIEWER)


def roles_for(role: str) -> list[str]:
    """The roles a stored member role grants (``owner`` also acts as ``admin`` and ``member``)."""
    return list(_EXPANSION[canonical_role(role)])


def expand_roles(claimed: list[str]) -> list[str]:
    """Roles claimed by a token or header, expanded like a stored role (highest wins).

    A claimed ``guest`` is a viewer: only a guest API token, which never passes through here,
    keeps the ``guest`` role.
    """
    return roles_for(primary_role(claimed))


def primary_role(roles: list[str]) -> str:
    """The highest canonical role in ``roles``."""
    held = {canonical_role(role) for role in roles}
    return next((role for role in ROLES if role in held), VIEWER)


def is_admin(roles: list[str]) -> bool:
    return ADMIN in roles or OWNER in roles


def can_write(roles: list[str]) -> bool:
    """Whether the identity may change anything (a viewer only reads)."""
    return any(role in (OWNER, ADMIN, MEMBER, GUEST) for role in roles)


def require_writer(roles: list[str], action: str) -> None:
    """403 for a viewer; ``action`` completes "Viewers cannot ..."."""
    if not can_write(roles):
        raise HTTPException(status_code=403, detail=f"Forbidden: viewers cannot {action}")


def invalidate_role(user_id: str) -> None:
    for key in [key for key in _cache if key.endswith(f"\x00{user_id}")]:
        _cache.pop(key, None)


def clear_role_cache() -> None:
    _cache.clear()


async def _stored_role(db_mgr: Any, user_id: str, org_id: str) -> tuple[bool, str | None]:
    """``(person exists, their role in org_id or None)``, cached briefly."""
    now = time.monotonic()
    key = f"{org_id}\x00{user_id}"
    cached = _cache.get(key)
    if cached is not None and cached[0] > now:
        stored = cached[1]
        return (stored is not None, stored[0] if stored and stored[0] else None)
    try:
        from api.db.models import UserModel
        from api.services.memberships import role_in

        async with db_mgr.session(tenant_org_id=org_id) as session:
            exists = await session.get(UserModel, user_id) is not None
            role = await role_in(session, user_id, org_id) if exists else None
    except Exception as err:
        # A database blip must not lock everybody out; fall back to the token's roles.
        logger.warning("Live role lookup failed for %s: %s", user_id, err)
        return False, None
    _cache[key] = (now + ROLE_CACHE_TTL_SECONDS, (role or "", org_id) if exists else None)
    return exists, role


async def with_live_roles(ctx: RequestContext, db_mgr: Any) -> RequestContext:
    """Return ``ctx`` with the role the person holds now in the token's organization.

    A role belongs to one organization (``memberships``): a person who is not a member of the
    token's organization (removed since, or never) gets no role there at all.
    """
    if (
        ctx.source != "session"
        or db_mgr is None
        or getattr(db_mgr, "session_factory", None) is None
    ):
        return ctx
    exists, role = await _stored_role(db_mgr, ctx.user_id, ctx.org_id)
    if not exists:
        return ctx
    if role is None:
        logger.warning("Person %s is not a member of their token's %s", ctx.user_id, ctx.org_id)
        return replace(ctx, roles=[])
    return replace(ctx, roles=roles_for(role))
