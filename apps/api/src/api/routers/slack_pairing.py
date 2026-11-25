"""Link a Slack account from the conversation it could not open, and confirm a pairing link."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity
from api.auth.origin import public_origin
from api.auth.roles import can_write
from api.services.memberships import member_display_name
from api.services.slack_bots import SlackBotStore
from api.services.slack_link import PairingCodes
from api.services.slack_pairing_request import (
    PairingRequestError,
    PairingTarget,
    pairing_target,
    send_pairing_request,
)

router = APIRouter(prefix="/v1/slack", tags=["Slack"])


class PairingContext(BaseModel):
    pairing_required: bool = Field(
        description="True when the session is a Slack direct message of a Slack user who has "
        "not linked an account: linking it is how its owner opens it."
    )
    slack_user_id: str | None = Field(default=None, description="That Slack user, when required")


class PairingRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)


class PairingRequestResult(BaseModel):
    status: str = Field(description="`sent`: the link is in the Slack user's direct messages")
    slack_user_id: str


class PairingPreview(BaseModel):
    slack_user_id: str
    team_id: str


async def _target_for(request: Request, session_id: str) -> PairingTarget | None:
    org_id, user_id, _ = _get_request_identity(request)
    record = await request.app.state.session_store.get_session(session_id)
    if record is None or record.tenant_org_id != org_id:
        return None
    return await pairing_target(
        getattr(request.app.state, "db_manager", None), org_id, record, user_id
    )


@router.get("/pairing-context", response_model=PairingContext)
async def get_pairing_context(request: Request, session_id: str = Query(...)) -> PairingContext:
    """Whether the signed-in member can open ``session_id`` by linking its Slack owner."""
    target = await _target_for(request, session_id)
    if target is None:
        return PairingContext(pairing_required=False)
    return PairingContext(pairing_required=True, slack_user_id=target.slack_user_id)


@router.post("/pairing-requests", response_model=PairingRequestResult, status_code=202)
async def request_pairing(body: PairingRequest, request: Request) -> PairingRequestResult:
    """Send the Slack owner of a direct-message session a link to pair their account.

    The link goes only to that Slack user, in their conversation with the bot that holds it;
    whoever confirms it links *their own* account to that Slack user.
    """
    org_id, user_id, roles = _get_request_identity(request)
    if not can_write(roles):
        raise HTTPException(
            status_code=403, detail="Forbidden: read-only role cannot link accounts"
        )
    target = await _target_for(request, body.session_id)
    if target is None:
        raise HTTPException(status_code=404, detail="This session cannot be opened by linking")
    db_mgr = getattr(request.app.state, "db_manager", None)
    try:
        await send_pairing_request(
            target=target,
            codes=getattr(request.app.state, "slack_pairing_codes", None) or PairingCodes(db_mgr),
            bot_store=getattr(request.app.state, "slack_bot_store", None) or SlackBotStore(db_mgr),
            org_id=org_id,
            base_url=public_origin(request),
            session_id=body.session_id,
            requester=await member_display_name(db_mgr, org_id, user_id) or "A colleague",
        )
    except PairingRequestError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    return PairingRequestResult(status="sent", slack_user_id=target.slack_user_id)


@router.get("/pair/preview", response_model=PairingPreview)
async def preview_pairing(
    request: Request, code: str = Query(..., min_length=4, max_length=32)
) -> PairingPreview:
    """Which Slack user a pairing code would link, so the member confirms what they link."""
    _get_request_identity(request)
    codes = getattr(request.app.state, "slack_pairing_codes", None) or PairingCodes(
        getattr(request.app.state, "db_manager", None)
    )
    found = await codes.peek(code)
    if found is None:
        raise HTTPException(status_code=400, detail="That link is invalid or has expired")
    return PairingPreview(slack_user_id=found[0], team_id=found[1])
