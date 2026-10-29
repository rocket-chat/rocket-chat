"""Live role resolution for session-cookie identities.

A signed session token carries the roles it was minted with and lives for days. Trusting
it alone means a demoted or removed admin keeps admin rights until the token expires, so
the database role is re-read (with a short cache) and overrides the token's claim.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any

from api.auth.context import RequestContext

logger = logging.getLogger(__name__)

ROLE_CACHE_TTL_SECONDS = 15.0
_ADMIN_ROLES = ["admin", "owner", "developer"]

# user_id -> (expiry, (role, org_id) of the stored member, or None when there is none)
_cache: dict[str, tuple[float, tuple[str, str] | None]] = {}


def roles_for(role: str) -> list[str]:
    """The roles a stored member role grants.

    A human ``guest`` is held to ``readonly``, so every read-only check covers it. Only a guest
    *token* keeps the ``guest`` role (``api_tokens``): it writes its own conversations, and the
    tenant middleware confines it to them (``guest_may_call``).
    """
    if role in ("admin", "owner"):
        return list(_ADMIN_ROLES)
    return ["readonly"] if role == "guest" else [role]


def invalidate_role(user_id: str) -> None:
    _cache.pop(user_id, None)


def clear_role_cache() -> None:
    _cache.clear()


async def _stored_member(db_mgr: Any, user_id: str) -> tuple[str, str] | None:
    now = time.monotonic()
    cached = _cache.get(user_id)
    if cached is not None and cached[0] > now:
        return cached[1]
    member: tuple[str, str] | None = None
    try:
        from api.db.models import UserModel

        async with db_mgr.session() as session:
            user = await session.get(UserModel, user_id)
            member = (user.role, user.org_id) if user is not None else None
    except Exception as err:
        # A database blip must not lock everybody out; fall back to the token's roles.
        logger.warning("Live role lookup failed for %s: %s", user_id, err)
        return None
    _cache[user_id] = (now + ROLE_CACHE_TTL_SECONDS, member)
    return member


async def with_live_roles(ctx: RequestContext, db_mgr: Any) -> RequestContext:
    """Return ``ctx`` with roles taken from the database for session-cookie identities.

    A role belongs to one organization: a member row stored under another organization than
    the token's grants nothing there, so the identity is reduced to ``readonly``.
    """
    if (
        ctx.source != "session"
        or db_mgr is None
        or getattr(db_mgr, "session_factory", None) is None
    ):
        return ctx
    member = await _stored_member(db_mgr, ctx.user_id)
    if member is None:
        return ctx
    role, org_id = member
    if org_id != ctx.org_id:
        logger.warning(
            "Member %s is stored in another organization than their token's %s",
            ctx.user_id,
            ctx.org_id,
        )
        return replace(ctx, roles=["readonly"])
    return replace(ctx, roles=roles_for(role))
