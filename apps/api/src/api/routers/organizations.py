"""The organizations a person belongs to: listing them, switching, and inviting people.

A session token names one active organization. Switching checks the person's membership
there and issues a new token; nothing else lets a token change organization.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity, _require_admin
from api.auth.capabilities import capabilities
from api.auth.identity import resolve_identity
from api.auth.roles import (
    ROLES,
    assignable_role,
    canonical_role,
    invalidate_role,
    primary_role,
    roles_for,
)
from api.auth.session_manager import create_session_token, set_session_cookie
from api.db.models import OrganizationModel
from api.services.audit import AuditService
from api.services.memberships import (
    INVITE_LIFETIME_DAYS,
    MAX_INVITE_LIFETIME_DAYS,
    Invite,
    InviteInvalid,
    accept_invite,
    create_invite,
    list_invites,
    memberships_of,
    revoke_invite,
    role_in,
)

router = APIRouter(prefix="/v1", tags=["Organizations"])


class OrganizationSummary(BaseModel):
    id: str
    name: str
    role: str
    active: bool = False


class OrganizationList(BaseModel):
    organizations: list[OrganizationSummary]


class CurrentOrganization(BaseModel):
    id: str
    name: str


class MyAccess(BaseModel):
    """What the caller may do here: their role and their role's capabilities."""

    org_id: str
    org_name: str
    role: str
    roles: list[str]
    capabilities: list[str]


class SwitchOrgRequest(BaseModel):
    org_id: str = Field(..., min_length=1, max_length=64)


class SwitchOrgResponse(BaseModel):
    org_id: str
    roles: list[str]


class CreateInviteRequest(BaseModel):
    email: str = Field(..., max_length=255)
    role: str = Field(default="member", description="One of owner | admin | member | viewer")
    lifetime_days: int = Field(default=INVITE_LIFETIME_DAYS, ge=1, le=MAX_INVITE_LIFETIME_DAYS)


class InviteSummary(BaseModel):
    id: str
    email: str
    role: str
    expires_at: int
    created_by: str
    created_at: int
    accepted_at: int | None = None


class CreatedInvite(BaseModel):
    invite: InviteSummary
    # Shown once: the link carries the secret, the database only its hash.
    token: str


class InviteList(BaseModel):
    invites: list[InviteSummary]


class AcceptInviteRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=300)


def _summary(invite: Invite) -> InviteSummary:
    return InviteSummary(
        id=invite.id,
        email=invite.email,
        role=invite.role,
        expires_at=invite.expires_at,
        created_by=invite.created_by,
        created_at=invite.created_at,
        accepted_at=invite.accepted_at,
    )


def _database(request: Request) -> Any:
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr is None or getattr(db_mgr, "session_factory", None) is None:
        raise HTTPException(status_code=503, detail="Database is not available")
    return db_mgr


@router.get("/organization", response_model=CurrentOrganization)
async def current_organization(request: Request) -> CurrentOrganization:
    """The caller's active organization, by name."""
    org_id, _, _ = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr is None or getattr(db_mgr, "session_factory", None) is None:
        return CurrentOrganization(id=org_id, name=org_id.replace("_", " ").title())
    async with db_mgr.session(tenant_org_id=org_id) as session:
        org = await session.get(OrganizationModel, org_id)
    return CurrentOrganization(id=org_id, name=org.name if org else org_id)


@router.get("/me/access", response_model=MyAccess)
async def my_access(request: Request) -> MyAccess:
    """The caller's role and capabilities in the active organization (for the cockpit: what
    to show; the API enforces each of them again)."""
    org_id, _, roles = _get_request_identity(request)
    current = await current_organization(request)
    held = await capabilities(getattr(request.app.state, "db_manager", None), org_id, roles)
    return MyAccess(
        org_id=org_id,
        org_name=current.name,
        role=primary_role(roles) if roles else "viewer",
        roles=roles,
        capabilities=sorted(c.value for c in held),
    )


@router.get("/me/organizations", response_model=OrganizationList)
async def my_organizations(request: Request) -> OrganizationList:
    """Every organization the caller belongs to, the active one marked."""
    org_id, user_id, roles = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    found = await memberships_of(db_mgr, user_id)
    listed = [
        OrganizationSummary(id=m.org_id, name=m.org_name, role=m.role, active=m.org_id == org_id)
        for m in found
    ]
    if not any(item.active for item in listed):
        current = await current_organization(request)
        listed.insert(
            0,
            OrganizationSummary(
                id=org_id, name=current.name, role=canonical_role(roles[0]), active=True
            ),
        )
    return OrganizationList(organizations=listed)


@router.post("/auth/switch-org", response_model=SwitchOrgResponse)
async def switch_organization(
    body: SwitchOrgRequest, request: Request, response: Response
) -> SwitchOrgResponse:
    """Make another organization the active one: allowed only for one the caller belongs to."""
    ctx = resolve_identity(request)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if ctx.source not in ("session", "dev"):
        raise HTTPException(status_code=400, detail="Only a signed-in session can switch")
    db_mgr = _database(request)
    async with db_mgr.session(tenant_org_id=body.org_id) as session:
        role = await role_in(session, ctx.user_id, body.org_id)
    if role is None:
        raise HTTPException(status_code=403, detail="You are not a member of that organization")
    roles = roles_for(role)
    token = create_session_token(
        user_id=ctx.user_id, org_id=body.org_id, email=ctx.email, name=ctx.name, roles=roles
    )
    set_session_cookie(response, token)
    invalidate_role(ctx.user_id)
    await AuditService(db_mgr).record(
        "login",
        org_id=body.org_id,
        actor_user_id=ctx.user_id,
        result_summary=f"switched from organization {ctx.org_id}",
    )
    return SwitchOrgResponse(org_id=body.org_id, roles=roles)


@router.get("/admin/invites", response_model=InviteList)
async def get_invites(request: Request) -> InviteList:
    """The organization's invitations, newest first (admin/owner only)."""
    org_id, _, _ = _require_admin(request)
    return InviteList(invites=[_summary(i) for i in await list_invites(_database(request), org_id)])


@router.post("/admin/invites", response_model=CreatedInvite)
async def post_invite(body: CreateInviteRequest, request: Request) -> CreatedInvite:
    """Invite a person by e-mail; the link expires and works once (admin/owner only)."""
    org_id, admin_id, roles = _require_admin(request)
    role = assignable_role(body.role) or ""
    if role not in ROLES:
        raise HTTPException(status_code=422, detail=f"Invalid role. Allowed: {', '.join(ROLES)}.")
    if role == "owner" and "owner" not in roles:
        raise HTTPException(status_code=403, detail="Only an owner can invite an owner")
    email = body.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=422, detail="Enter a valid e-mail address.")
    db_mgr = _database(request)
    invite, token = await create_invite(db_mgr, org_id, email, role, admin_id, body.lifetime_days)
    await AuditService(db_mgr).record(
        "settings_change",
        org_id=org_id,
        actor_user_id=admin_id,
        result_summary=f"invited {email} as {role}",
        metadata={"invite_id": invite.id, "role": role},
    )
    return CreatedInvite(invite=_summary(invite), token=token)


@router.delete("/admin/invites/{invite_id}")
async def delete_invite(invite_id: str, request: Request) -> dict[str, str]:
    """Withdraw an invitation (admin/owner only)."""
    org_id, admin_id, _ = _require_admin(request)
    db_mgr = _database(request)
    if not await revoke_invite(db_mgr, org_id, invite_id):
        raise HTTPException(status_code=404, detail="Invitation not found")
    await AuditService(db_mgr).record(
        "settings_change",
        org_id=org_id,
        actor_user_id=admin_id,
        result_summary="withdrew an invitation",
        metadata={"invite_id": invite_id},
    )
    return {"status": "deleted"}


@router.post("/invites/accept", response_model=OrganizationSummary)
async def accept(body: AcceptInviteRequest, request: Request) -> OrganizationSummary:
    """Join the invitation's organization; switch to it with ``POST /v1/auth/switch-org``."""
    ctx = resolve_identity(request)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Sign in to accept the invitation")
    if ctx.source == "guest_token":
        raise HTTPException(status_code=403, detail="A guest token cannot join an organization")
    db_mgr = _database(request)
    try:
        membership = await accept_invite(db_mgr, body.token, ctx.user_id, ctx.email)
    except InviteInvalid as err:
        raise HTTPException(status_code=410, detail=str(err)) from err
    await AuditService(db_mgr).record(
        "settings_change",
        org_id=membership.org_id,
        actor_user_id=ctx.user_id,
        result_summary=f"joined the organization as {membership.role}",
    )
    return OrganizationSummary(id=membership.org_id, name=membership.org_name, role=membership.role)
