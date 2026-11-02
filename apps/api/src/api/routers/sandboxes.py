"""The sandbox catalogue: what members can pick, and what administrators offer.

A team's administrators offer types of their own to the team (``/v1/teams/{id}/sandboxes``):
its members' sessions and its agents use them, nobody else sees them. Only organization
administrators choose a placement profile, and a team's type never asks for more CPU, memory
or disk than the organization's own sandbox settings allow."""

from __future__ import annotations

from typing import Any

from config_engine.policy import quantity
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sandbox_driver.profiles import configured_profiles

from api.auth.access import _require_admin
from api.services.audit import AuditService
from api.services.ownership import request_viewer
from api.services.sandbox_catalogue import (
    CAPABILITIES,
    CatalogueError,
    SandboxCatalogue,
    SandboxEntry,
    env_view,
)
from api.services.settings import effective_settings
from api.services.teams import get_team
from specifications.interfaces.ownership import Owner, Viewer, can_manage

router = APIRouter(prefix="/v1", tags=["Sandboxes"])


class SandboxEnvVar(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    value: str | None = Field(
        None,
        max_length=8192,
        description="The value; never returned for a secret, and left empty to keep a secret",
    )
    secret: bool = Field(False, description="Write-only: stored encrypted, never shown again")


class SandboxView(BaseModel):
    id: str
    name: str
    image: str
    description: str
    capabilities: list[str]
    is_default: bool
    profile: str = Field("", description="Operator-defined placement profile; empty: none")
    cpu_limit: str = Field("", description="CPU cores for this type; empty inherits")
    memory_limit: str = Field("", description="Memory for this type (e.g. 8Gi); empty inherits")
    disk_size: str = Field("", description="Workspace volume size (e.g. 50Gi); empty inherits")
    release_after_days: int | None = Field(
        None, description="Days an idle session keeps its workspace; empty inherits"
    )
    env: list[SandboxEnvVar] = Field(
        default_factory=list,
        description="Variables set for every command (those who manage it only; secrets masked)",
    )
    team_id: str | None = Field(None, description="The team that owns it; empty: organization")


class SandboxProfileView(BaseModel):
    name: str
    description: str


class SandboxCatalogueView(BaseModel):
    sandboxes: list[SandboxView]
    capabilities: dict[str, str] = Field(description="Every capability tag and what it needs")


class SandboxBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    image: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=500)
    capabilities: list[str] = Field(default_factory=list)
    is_default: bool = False
    profile: str = Field(default="", max_length=64)
    cpu_limit: str = Field(default="", max_length=16)
    memory_limit: str = Field(default="", max_length=16)
    disk_size: str = Field(default="", max_length=16)
    release_after_days: int | None = None
    env: list[SandboxEnvVar] = Field(default_factory=list, max_length=50)


class SandboxPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    image: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)
    capabilities: list[str] | None = None
    is_default: bool | None = None
    profile: str | None = Field(default=None, max_length=64)
    cpu_limit: str | None = Field(default=None, max_length=16)
    memory_limit: str | None = Field(default=None, max_length=16)
    disk_size: str | None = Field(default=None, max_length=16)
    release_after_days: int | None = None
    env: list[SandboxEnvVar] | None = Field(default=None, max_length=50)


def catalogue_for(request: Request) -> SandboxCatalogue:
    return SandboxCatalogue(getattr(request.app.state, "db_manager", None))


def _view(entry: SandboxEntry, *, admin: bool = True) -> SandboxView:
    fields = {k: getattr(entry, k) for k in SandboxView.model_fields if k != "env"}
    env = [SandboxEnvVar(**v) for v in env_view(entry)] if admin else []
    return SandboxView(**fields, env=env)


async def _audit(request: Request, org_id: str, user_id: str, action: str, entry_id: str) -> None:
    await AuditService(getattr(request.app.state, "db_manager", None)).record(
        "settings_change",
        org_id=org_id,
        actor_user_id=user_id,
        result_summary=f"sandbox catalogue: {action}",
        metadata={"domain": "sandbox_catalogue", "sandbox_id": entry_id, "action": action},
    )


@router.get("/sandboxes", response_model=SandboxCatalogueView)
async def list_sandboxes(request: Request) -> SandboxCatalogueView:
    """The types the caller may pick: the organization's and their teams' (every team's for an
    administrator); the variables only of the types they manage."""
    org_id, viewer = await request_viewer(request)
    entries = await catalogue_for(request).entries(
        org_id, teams=None if viewer.is_org_admin else viewer.teams
    )
    return SandboxCatalogueView(
        sandboxes=[_view(e, admin=can_manage(viewer, _owner(e))) for e in entries],
        capabilities=CAPABILITIES,
    )


def _owner(entry: SandboxEntry) -> Owner:
    return Owner("team", team_id=entry.team_id) if entry.team_id else Owner("org")


@router.get("/admin/sandboxes/profiles", response_model=list[SandboxProfileView])
async def list_sandbox_profiles(request: Request) -> list[SandboxProfileView]:
    """The placement profiles this deployment's operator defined, to choose per sandbox type."""
    _require_admin(request)
    return [
        SandboxProfileView(name=p.name, description=p.description)
        for p in configured_profiles().values()
    ]


@router.post("/admin/sandboxes", response_model=SandboxView)
async def create_sandbox(body: SandboxBody, request: Request) -> SandboxView:
    org_id, user_id, _ = _require_admin(request)
    try:
        entry = await catalogue_for(request).create(org_id, **body.model_dump())
    except CatalogueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    await _audit(request, org_id, user_id, "created", entry.id)
    return _view(entry)


@router.patch("/admin/sandboxes/{sandbox_id}", response_model=SandboxView)
async def update_sandbox(sandbox_id: str, body: SandboxPatch, request: Request) -> SandboxView:
    org_id, user_id, _ = _require_admin(request)
    try:
        entry = await catalogue_for(request).update(
            org_id, sandbox_id, **body.model_dump(exclude_unset=True)
        )
    except CatalogueError as err:
        status = 404 if str(err) == "No such sandbox." else 422
        raise HTTPException(status_code=status, detail=str(err)) from err
    await _audit(request, org_id, user_id, "updated", sandbox_id)
    return _view(entry)


@router.delete("/admin/sandboxes/{sandbox_id}")
async def delete_sandbox(sandbox_id: str, request: Request) -> dict[str, str]:
    org_id, user_id, _ = _require_admin(request)
    try:
        await catalogue_for(request).delete(org_id, sandbox_id)
    except CatalogueError as err:
        status = 404 if str(err) == "No such sandbox." else 409
        raise HTTPException(status_code=status, detail=str(err)) from err
    await _audit(request, org_id, user_id, "deleted", sandbox_id)
    return {"status": "deleted"}


# -- a team's own types ---------------------------------------------------------------------


async def _team_manager(request: Request, team_id: str) -> tuple[str, Viewer]:
    org_id, viewer = await request_viewer(request)
    if await get_team(getattr(request.app.state, "db_manager", None), org_id, team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found.")
    if not can_manage(viewer, Owner("team", team_id=team_id)):
        raise HTTPException(status_code=403, detail="Only the team's administrators can do this.")
    return org_id, viewer


async def _within_the_organization(
    request: Request, org_id: str, viewer: Viewer, values: dict[str, Any]
) -> None:
    """A team administrator's type stays inside the organization's sandbox settings: no
    placement profile, and no more CPU, memory or disk than the organization gives a sandbox."""
    if viewer.is_org_admin:
        return
    if values.get("profile"):
        raise HTTPException(
            status_code=403, detail="Only organization administrators choose a placement profile."
        )
    sandboxes = await effective_settings(
        getattr(request.app.state, "db_manager", None), org_id, "sandboxes"
    )
    for key in ("cpu_limit", "memory_limit", "disk_size"):
        wanted, ceiling = values.get(key), sandboxes.get(key)
        if not wanted or not ceiling:
            continue
        amount, limit = quantity(wanted), quantity(ceiling)
        if amount is None or limit is None or amount > limit:
            raise HTTPException(
                status_code=422,
                detail=f"{key} {wanted} is above the organization's {ceiling}.",
            )


async def _team_entry(request: Request, org_id: str, team_id: str, sandbox_id: str) -> SandboxEntry:
    entries = await catalogue_for(request).entries(org_id, teams=frozenset({team_id}))
    found = next((e for e in entries if e.id == sandbox_id and e.team_id == team_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="No such sandbox.")
    return found


@router.post("/teams/{team_id}/sandboxes", response_model=SandboxView)
async def create_team_sandbox(team_id: str, body: SandboxBody, request: Request) -> SandboxView:
    """Offer a sandbox type to the team (its administrators)."""
    org_id, viewer = await _team_manager(request, team_id)
    values = body.model_dump()
    await _within_the_organization(request, org_id, viewer, values)
    try:
        entry = await catalogue_for(request).create(org_id, **values, team_id=team_id)
    except CatalogueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    await _audit(request, org_id, viewer.user_id, f"created for team {team_id}", entry.id)
    return _view(entry)


@router.patch("/teams/{team_id}/sandboxes/{sandbox_id}", response_model=SandboxView)
async def update_team_sandbox(
    team_id: str, sandbox_id: str, body: SandboxPatch, request: Request
) -> SandboxView:
    org_id, viewer = await _team_manager(request, team_id)
    await _team_entry(request, org_id, team_id, sandbox_id)
    values = body.model_dump(exclude_unset=True)
    await _within_the_organization(request, org_id, viewer, values)
    try:
        entry = await catalogue_for(request).update(org_id, sandbox_id, **values)
    except CatalogueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    await _audit(request, org_id, viewer.user_id, f"updated for team {team_id}", sandbox_id)
    return _view(entry)


@router.delete("/teams/{team_id}/sandboxes/{sandbox_id}")
async def delete_team_sandbox(team_id: str, sandbox_id: str, request: Request) -> dict[str, str]:
    org_id, viewer = await _team_manager(request, team_id)
    await _team_entry(request, org_id, team_id, sandbox_id)
    try:
        await catalogue_for(request).delete(org_id, sandbox_id)
    except CatalogueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    await _audit(request, org_id, viewer.user_id, f"deleted for team {team_id}", sandbox_id)
    return {"status": "deleted"}
