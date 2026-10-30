"""Roles and capabilities: one place that answers "may this person do that?".

A member holds one role in an organization: ``owner``, ``admin``, ``member`` or ``viewer``.
``guest`` is internal: guest API tokens and Slack guests, confined elsewhere. Older names are
read as their successors (``developer`` -> ``member``, ``readonly`` and a human ``guest`` ->
``viewer``), so a token minted before the rename keeps working with the same rights.

What a role may do beyond reading and writing its own work is a *capability*, granted per
role in the organization's ``access`` settings. Owners hold every capability, so an
organization can never lock its owners out of their own policy.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import HTTPException, Request

from api.auth.access import _get_request_identity
from api.auth.roles import MEMBER, OWNER, can_write, primary_role, roles_for
from api.services.memberships import role_in
from api.services.settings import effective_settings


class Capability(StrEnum):
    CREATE_PERSONAL_AGENTS = "create_personal_agents"
    BRING_OWN_KEYS = "bring_own_keys"
    USE_BYPASS_MODE = "use_bypass_mode"
    REGISTER_MCP = "register_mcp"
    PUBLISH_AS_BOT = "publish_as_bot"
    MANAGE_SPEND_LIMITS = "manage_spend_limits"


async def capabilities(db_mgr: Any, org_id: str, roles: list[str]) -> frozenset[Capability]:
    """The capabilities the organization's ``access`` settings give these roles."""
    role = primary_role(roles)
    if role == OWNER:
        return frozenset(Capability)
    if not can_write(roles):
        return frozenset()
    access = await effective_settings(db_mgr, org_id, "access")
    return frozenset(cap for cap in Capability if role in (access.get(cap.value) or ()))


async def has_capability(
    db_mgr: Any, org_id: str, roles: list[str], capability: Capability
) -> bool:
    return capability in await capabilities(db_mgr, org_id, roles)


async def member_has(db_mgr: Any, org_id: str, user_id: str, capability: Capability) -> bool:
    """Whether the person's current role in ``org_id`` holds ``capability`` (outside a request:
    the gateway, the turn engine). Without a database every member holds the defaults."""
    if db_mgr is None or getattr(db_mgr, "session_factory", None) is None:
        return await has_capability(db_mgr, org_id, roles_for(MEMBER), capability)
    async with db_mgr.session(tenant_org_id=org_id) as session:
        role = await role_in(session, user_id, org_id)
    return role is not None and await has_capability(db_mgr, org_id, roles_for(role), capability)


async def require(request: Request, capability: Capability) -> tuple[str, str, list[str]]:
    """The caller's identity if their role holds ``capability``, else 403 naming it."""
    org_id, user_id, roles = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    if not await has_capability(db_mgr, org_id, roles, capability):
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden: your role does not have the {capability.value} capability",
        )
    return org_id, user_id, roles
