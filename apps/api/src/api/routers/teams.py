"""Teams: groups of an organization's members that own agents, skills, MCP servers and knowledge.

Every member lists the organization's teams (owner pickers name them). Organization
administrators create and delete teams; a team's administrators (and the organization's)
rename it and manage its members. Members of a team, and administrators, see who is in it.
"""

from __future__ import annotations

from typing import Any

from config_engine import validate as validate_setting
from config_engine.registry import specs_for, writable_keys
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity
from api.auth.access import _require_admin as require_admin
from api.services.audit import AuditService
from api.services.memberships import members
from api.services.settings import (
    load_org_config,
    load_team_config,
    resolve_settings,
    save_team_config,
)
from api.services.teams import (
    Team,
    TeamError,
    TeamMember,
    create_team,
    delete_team,
    get_team,
    list_teams,
    owned_counts,
    remove_team_member,
    set_team_member,
    team_members,
    update_team,
    viewer_for,
)
from specifications.interfaces.ownership import Owner, Viewer, can_manage

router = APIRouter(prefix="/v1", tags=["Teams"])


class TeamView(BaseModel):
    id: str
    name: str
    description: str
    member_count: int
    # The caller's role in the team (``admin``, ``member``), or ``None`` when not in it.
    my_role: str | None = None
    # Whether the caller may rename the team and manage its members and objects.
    can_manage: bool = False


class TeamList(BaseModel):
    teams: list[TeamView]


class TeamBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=255)


class TeamPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=255)


class TeamMemberView(BaseModel):
    user_id: str
    email: str
    name: str
    role: str


class TeamMemberList(BaseModel):
    members: list[TeamMemberView]
    # What the team owns, by kind (only kinds it owns some of).
    owned: dict[str, int] = Field(default_factory=dict)


class TeamMemberBody(BaseModel):
    role: str = Field(default="member", description="admin | member")


class CandidateList(BaseModel):
    members: list[TeamMemberView]


def _database(request: Request) -> Any:
    return getattr(request.app.state, "db_manager", None)


def _http(err: TeamError) -> HTTPException:
    return HTTPException(status_code=err.status_code, detail=err.detail)


def _view(team: Team, viewer: Viewer) -> TeamView:
    role = None
    if team.id in viewer.admin_teams:
        role = "admin"
    elif team.id in viewer.teams:
        role = "member"
    return TeamView(
        id=team.id,
        name=team.name,
        description=team.description,
        member_count=team.member_count,
        my_role=role,
        can_manage=can_manage(viewer, Owner("team", team_id=team.id)),
    )


def _member_view(member: TeamMember) -> TeamMemberView:
    return TeamMemberView(
        user_id=member.user_id, email=member.email, name=member.name, role=member.role
    )


async def _viewer(request: Request) -> tuple[str, Viewer]:
    org_id, user_id, roles = _get_request_identity(request)
    return org_id, await viewer_for(_database(request), org_id, user_id, roles)


async def _managed_team(request: Request, team_id: str) -> tuple[str, Viewer, Team]:
    """The team, when the caller may manage it (403 otherwise, 404 when it does not exist)."""
    org_id, viewer = await _viewer(request)
    team = await get_team(_database(request), org_id, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found.")
    if not can_manage(viewer, Owner("team", team_id=team_id)):
        raise HTTPException(status_code=403, detail="Only the team's administrators can do this.")
    return org_id, viewer, team


async def _audit(
    request: Request, org_id: str, actor: str, event: str, details: dict[str, Any]
) -> None:
    audit = getattr(request.app.state, "audit_service", None)
    if isinstance(audit, AuditService):
        await audit.record(event, org_id=org_id, actor_user_id=actor, metadata=details)


@router.get("/teams", response_model=TeamList)
async def get_teams(request: Request) -> TeamList:
    """The organization's teams, with the caller's role in each."""
    org_id, viewer = await _viewer(request)
    teams = await list_teams(_database(request), org_id)
    return TeamList(teams=[_view(team, viewer) for team in teams])


@router.post("/teams", response_model=TeamView, status_code=201)
async def post_team(body: TeamBody, request: Request) -> TeamView:
    """Create a team (organization administrators only)."""
    org_id, user_id, roles = require_admin(request)
    try:
        team = await create_team(_database(request), org_id, body.name, body.description, user_id)
    except TeamError as err:
        raise _http(err) from err
    await _audit(request, org_id, user_id, "team.created", {"team_id": team.id, "name": team.name})
    viewer = await viewer_for(_database(request), org_id, user_id, roles)
    return _view(team, viewer)


@router.patch("/teams/{team_id}", response_model=TeamView)
async def patch_team(team_id: str, body: TeamPatch, request: Request) -> TeamView:
    """Rename a team or change its description (its administrators, or the organization's)."""
    org_id, viewer, _ = await _managed_team(request, team_id)
    try:
        team = await update_team(_database(request), org_id, team_id, body.name, body.description)
    except TeamError as err:
        raise _http(err) from err
    await _audit(request, org_id, viewer.user_id, "team.updated", {"team_id": team_id})
    return _view(team, viewer)


@router.delete("/teams/{team_id}", status_code=204)
async def remove_team(team_id: str, request: Request) -> None:
    """Delete a team that owns nothing (organization administrators only)."""
    org_id, user_id, _ = require_admin(request)
    try:
        await delete_team(_database(request), org_id, team_id)
    except TeamError as err:
        raise _http(err) from err
    await _audit(request, org_id, user_id, "team.deleted", {"team_id": team_id})


@router.get("/teams/{team_id}/members", response_model=TeamMemberList)
async def get_team_members(team_id: str, request: Request) -> TeamMemberList:
    """Who is in the team, and what it owns (its members and administrators)."""
    org_id, viewer = await _viewer(request)
    if team_id not in viewer.teams and not viewer.is_org_admin:
        raise HTTPException(status_code=404, detail="Team not found.")
    db_mgr = _database(request)
    try:
        found = await team_members(db_mgr, org_id, team_id)
    except TeamError as err:
        raise _http(err) from err
    return TeamMemberList(
        members=[_member_view(m) for m in found], owned=await owned_counts(db_mgr, org_id, team_id)
    )


@router.get("/teams/{team_id}/candidates", response_model=CandidateList)
async def get_candidates(
    team_id: str,
    request: Request,
    q: str | None = Query(default=None, description="Filter by e-mail or name (substring)."),
) -> CandidateList:
    """Members of the organization a team administrator can add (not already in the team)."""
    org_id, _, _ = await _managed_team(request, team_id)
    db_mgr = _database(request)
    if db_mgr is None or getattr(db_mgr, "session_factory", None) is None:
        return CandidateList(members=[])
    present = {m.user_id for m in await team_members(db_mgr, org_id, team_id)}
    async with db_mgr.session(tenant_org_id=org_id) as session:
        found, _ = await members(session, org_id, q=q, role=None, limit=50)
    return CandidateList(
        members=[
            TeamMemberView(user_id=m.user_id, email=m.email, name=m.name, role=m.role)
            for m in found
            if m.user_id not in present and m.role != "guest"
        ]
    )


@router.put("/teams/{team_id}/members/{user_id}", response_model=TeamMemberView)
async def put_team_member(
    team_id: str, user_id: str, body: TeamMemberBody, request: Request
) -> TeamMemberView:
    """Add a member of the organization to the team, or change their team role."""
    org_id, viewer, _ = await _managed_team(request, team_id)
    try:
        member = await set_team_member(_database(request), org_id, team_id, user_id, body.role)
    except TeamError as err:
        raise _http(err) from err
    await _audit(
        request,
        org_id,
        viewer.user_id,
        "team.member_changed",
        {"team_id": team_id, "user_id": user_id, "role": body.role},
    )
    return _member_view(member)


@router.delete("/teams/{team_id}/members/{user_id}", status_code=204)
async def delete_team_member(team_id: str, user_id: str, request: Request) -> None:
    """Remove someone from the team (its administrators), or leave it (oneself)."""
    org_id, viewer = await _viewer(request)
    if user_id != viewer.user_id and not can_manage(viewer, Owner("team", team_id=team_id)):
        raise HTTPException(status_code=403, detail="Only the team's administrators can do this.")
    if not await remove_team_member(_database(request), org_id, team_id, user_id):
        raise HTTPException(status_code=404, detail="Not a member of this team.")
    await _audit(
        request,
        org_id,
        viewer.user_id,
        "team.member_changed",
        {"team_id": team_id, "user_id": user_id, "role": None},
    )


# -- a team's own defaults ------------------------------------------------------------------


class TeamSettingsBody(BaseModel):
    config: dict[str, Any] = Field(
        default_factory=dict, description="The team's defaults; only organization defaults"
    )


class TeamSettingsView(BaseModel):
    domain: str
    # The team's own values.
    config: dict[str, Any]
    # The keys a team may set in this domain, minus those the organization locked.
    writable: list[str]
    locked_by_org: list[str]
    # Every key's value in the team's context (before members' own choices) and its source.
    effective: dict[str, Any]
    provenance: dict[str, dict[str, Any]]


async def _team_settings_view(
    db_mgr: Any, org_id: str, team_id: str, domain: str
) -> TeamSettingsView:
    _, locked = await load_org_config(db_mgr, org_id, domain)
    keys = writable_keys(domain, "team")
    resolved = await resolve_settings(db_mgr, org_id, domain, team_id=team_id)
    return TeamSettingsView(
        domain=domain,
        config=await load_team_config(db_mgr, org_id, team_id, domain),
        writable=sorted(keys - set(locked)),
        locked_by_org=sorted(keys & set(locked)),
        effective=resolved.effective,
        provenance={k: v.to_dict() for k, v in resolved.provenance.items()},
    )


@router.get("/teams/{team_id}/settings/{domain}", response_model=TeamSettingsView)
async def get_team_settings(team_id: str, domain: str, request: Request) -> TeamSettingsView:
    """The team's defaults for a domain, used in the turns of the team's agents (its
    administrators). A member's own choice still wins; the organization's locks still hold."""
    org_id, _, _ = await _managed_team(request, team_id)
    if not specs_for(domain):
        raise HTTPException(status_code=404, detail=f"Unknown settings domain '{domain}'.")
    return await _team_settings_view(_database(request), org_id, team_id, domain)


@router.put("/teams/{team_id}/settings/{domain}", response_model=TeamSettingsView)
async def put_team_settings(
    team_id: str, domain: str, body: TeamSettingsBody, request: Request
) -> TeamSettingsView:
    """Replace the team's defaults for a domain (organization defaults only, not a key the
    organization locked)."""
    org_id, viewer, _ = await _managed_team(request, team_id)
    if not specs_for(domain):
        raise HTTPException(status_code=404, detail=f"Unknown settings domain '{domain}'.")
    db_mgr = _database(request)
    checked = validate_setting(domain, body.config, writer="team")
    _, locked = await load_org_config(db_mgr, org_id, domain)
    errors = [
        *(f"{e.key}: {e.message}" for e in checked.errors),
        *(f"{key}: locked by the organization" for key in body.config if key in locked),
    ]
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    await save_team_config(db_mgr, org_id, team_id, domain, checked.values)
    await _audit(
        request,
        org_id,
        viewer.user_id,
        "settings_change",
        {"team_id": team_id, "domain": domain, "keys": sorted(checked.values)},
    )
    return await _team_settings_view(db_mgr, org_id, team_id, domain)
