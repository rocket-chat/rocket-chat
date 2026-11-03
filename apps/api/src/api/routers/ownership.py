"""Moving objects between owners: what a move would do, and the moves that have no form.

``POST /v1/ownership/preview`` says, for any owned object and a destination owner, who would use
it, whether the caller may move it, and what would break or narrow (``services/ownership_moves``).
Agents, skills, MCP servers, knowledge bases, missions and templates move through their own
forms (re-saved with another owner); provider keys and sandbox types move here, refused while
anything would break.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.routers.credentials import CredentialView, _audit, _view, credential_store
from api.routers.sandboxes import SandboxView, catalogue_for
from api.routers.sandboxes import _audit as _audit_sandbox
from api.routers.sandboxes import _view as _sandbox_view
from api.services.audit import AuditService
from api.services.model_credentials import OwnerKind
from api.services.ownership import OwnershipError, request_viewer, requested_owner
from api.services.ownership_moves import MoveError, MovePreview, preview_move
from api.services.sandbox_catalogue import CatalogueError
from specifications.interfaces.ownership import Owner, Viewer

router = APIRouter(prefix="/v1", tags=["Ownership"])


class MoveBody(BaseModel):
    scope: str = Field(..., description="The new owner: 'user', 'team' or 'org'")
    team_id: str | None = None


class PreviewBody(MoveBody):
    kind: str = Field(
        ...,
        description="agent | skill | mcp_server | knowledge_base | credential | sandbox | "
        "mission | template",
    )
    id: str = Field(..., max_length=64)


class ProblemView(BaseModel):
    kind: str
    id: str
    name: str
    detail: str
    breaks: bool


class PreviewView(BaseModel):
    audience: str = Field(..., description="Who would use it after the move")
    allowed: bool
    reason: str = ""
    problems: list[ProblemView] = Field(default_factory=list)


def _preview_view(preview: MovePreview) -> PreviewView:
    return PreviewView(
        audience=preview.audience,
        allowed=preview.allowed,
        reason=preview.reason,
        problems=[ProblemView(**vars(p)) for p in preview.problems],
    )


async def _destination(request: Request, org_id: str, viewer: Viewer, body: MoveBody) -> Owner:
    try:
        return await requested_owner(
            getattr(request.app.state, "db_manager", None), org_id, viewer, body.scope, body.team_id
        )
    except OwnershipError as err:
        raise err.as_http() from err


async def _checked(
    request: Request, kind: str, object_id: str, body: MoveBody
) -> tuple[str, Viewer, Owner, MovePreview]:
    org_id, viewer = await request_viewer(request)
    dest = await _destination(request, org_id, viewer, body)
    try:
        preview = await preview_move(request.app, org_id, viewer, kind, object_id, dest)
    except MoveError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    return org_id, viewer, dest, preview


def _refuse(preview: MovePreview) -> None:
    if preview.allowed:
        return
    detail: dict[str, Any] = {
        "reason": preview.reason,
        "problems": [vars(p) for p in preview.problems],
    }
    raise HTTPException(status_code=409 if preview.problems else 403, detail=detail)


@router.post("/ownership/preview", response_model=PreviewView)
async def preview(body: PreviewBody, request: Request) -> PreviewView:
    """What moving an object to another owner would do (nothing is moved)."""
    _, _, _, found = await _checked(request, body.kind, body.id, body)
    return _preview_view(found)


@router.post("/credentials/{credential_id}/move", response_model=CredentialView)
async def move_credential(credential_id: str, body: MoveBody, request: Request) -> CredentialView:
    """Give a provider key to another owner (managing both sides); refused (409, with what
    would break) while an agent bound to it would lose it."""
    org_id, viewer, dest, found = await _checked(request, "credential", credential_id, body)
    _refuse(found)
    kind: OwnerKind = (
        "member" if dest.scope == "user" else ("team" if dest.scope == "team" else "org")
    )
    moved = await credential_store(request).move(
        org_id, credential_id, kind, dest.user_id, dest.team_id
    )
    if moved is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    await _audit(request, org_id, viewer.user_id, f"moved a credential to {dest.scope}", moved)
    await _audit_move(request, org_id, viewer.user_id, "credential", credential_id, dest)
    return _view(moved)


@router.post("/sandboxes/{sandbox_id}/move", response_model=SandboxView)
async def move_sandbox(sandbox_id: str, body: MoveBody, request: Request) -> SandboxView:
    """Give a sandbox type to a team or the organization (managing both sides); refused (409,
    with what would break) while an agent or mission outside the team starts in it."""
    org_id, viewer, dest, found = await _checked(request, "sandbox", sandbox_id, body)
    _refuse(found)
    try:
        entry = await catalogue_for(request).move(org_id, sandbox_id, dest.team_id)
    except CatalogueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    await _audit_sandbox(request, org_id, viewer.user_id, f"moved to {dest.scope}", sandbox_id)
    await _audit_move(request, org_id, viewer.user_id, "sandbox", sandbox_id, dest)
    return _sandbox_view(entry)


async def _audit_move(
    request: Request, org_id: str, actor: str, kind: str, object_id: str, dest: Owner
) -> None:
    await AuditService(getattr(request.app.state, "db_manager", None)).record(
        "ownership.moved",
        org_id=org_id,
        actor_user_id=actor,
        metadata={"kind": kind, "id": object_id, "scope": dest.scope, "team_id": dest.team_id},
    )
