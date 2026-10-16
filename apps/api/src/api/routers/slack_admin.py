"""Slack account linking and workspace binding endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity, _require_admin
from api.services.audit import AuditService
from api.services.slack_bots import SlackBotStore
from api.services.slack_link import (
    PairingCodes,
    adopt_guest_sessions,
    bind_workspace,
    link_member,
    unbind_workspace,
    workspace_org,
)

router = APIRouter(prefix="/v1", tags=["Slack"])


class PairRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=32, description="Code from /rocket-pair")


class WorkspaceBinding(BaseModel):
    connected_team_id: str | None
    bound_org_id: str | None
    bound_to_this_org: bool


async def _connected_team(request: Request, org_id: str) -> str | None:
    """The workspace the organization's bots belong to (``auth.test`` on save)."""
    store = getattr(request.app.state, "slack_bot_store", None) or SlackBotStore(
        getattr(request.app.state, "db_manager", None)
    )
    return next((b.team_id for b in await store.list(org_id) if b.team_id), None)


@router.post("/slack/pair")
async def complete_slack_pairing(body: PairRequest, request: Request) -> dict[str, Any]:
    """Finish linking: the signed-in member claims the Slack user that issued the code."""
    org_id, user_id, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(
            status_code=403, detail="Forbidden: read-only role cannot link accounts"
        )
    # Built per request from the shared store: this replica may not hold the Slack connection
    # that issued the code, and the code lives in the database, not in any one process.
    codes = getattr(request.app.state, "slack_pairing_codes", None) or PairingCodes(
        getattr(request.app.state, "db_manager", None)
    )
    claimed = await codes.complete(body.code)
    if claimed is None:
        raise HTTPException(status_code=400, detail="That code is invalid or has expired")
    slack_user_id, team_id = claimed

    # A workspace bound to another organization cannot link into this one.
    owner = await workspace_org(getattr(request.app.state, "db_manager", None), team_id)
    if owner is not None and owner != org_id:
        raise HTTPException(
            status_code=403, detail="This Slack workspace belongs to another organization"
        )
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr is None:
        raise HTTPException(status_code=503, detail="Account linking needs the PostgreSQL store")
    if not await link_member(db_mgr, user_id, org_id, slack_user_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "Your account is not a member of the organization you are signed in to, so it "
                "cannot be linked. Ask an administrator to check your organization."
            ),
        )
    adopted = await adopt_guest_sessions(db_mgr, org_id, team_id, slack_user_id, user_id)
    await AuditService(db_mgr).record(
        "credential_change",
        org_id=org_id,
        actor_user_id=user_id,
        result_summary="linked Slack account",
        metadata={
            "provider": "slack",
            "slack_user_id": slack_user_id,
            "sessions_adopted": adopted,
        },
    )
    return {"status": "linked", "slack_user_id": slack_user_id, "sessions_adopted": adopted}


@router.get("/admin/slack/workspace", response_model=WorkspaceBinding)
async def get_workspace_binding(request: Request) -> WorkspaceBinding:
    org_id, _, _ = _require_admin(request)
    team_id = await _connected_team(request, org_id)
    bound = await workspace_org(getattr(request.app.state, "db_manager", None), team_id or "")
    return WorkspaceBinding(
        connected_team_id=team_id, bound_org_id=bound, bound_to_this_org=bound == org_id
    )


@router.put("/admin/slack/workspace", response_model=WorkspaceBinding)
async def bind_connected_workspace(request: Request) -> WorkspaceBinding:
    """Bind the currently connected Slack workspace to the administrator's organization."""
    org_id, admin_id, _ = _require_admin(request)
    team_id = await _connected_team(request, org_id)
    if not team_id:
        raise HTTPException(status_code=409, detail="No Slack workspace is connected")
    db_mgr = getattr(request.app.state, "db_manager", None)
    try:
        await bind_workspace(db_mgr, team_id, org_id, admin_id)
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    return WorkspaceBinding(connected_team_id=team_id, bound_org_id=org_id, bound_to_this_org=True)


@router.delete("/admin/slack/workspace", response_model=WorkspaceBinding)
async def unbind_connected_workspace(request: Request) -> WorkspaceBinding:
    org_id, _, _ = _require_admin(request)
    team_id = await _connected_team(request, org_id) or ""
    await unbind_workspace(getattr(request.app.state, "db_manager", None), team_id, org_id)
    bound = await workspace_org(getattr(request.app.state, "db_manager", None), team_id)
    return WorkspaceBinding(
        connected_team_id=team_id or None, bound_org_id=bound, bound_to_this_org=bound == org_id
    )
