"""Scheduled background missions API.

Members manage their own missions; administrators can see and manage every mission in the
organization. A mission runs as an ordinary session owned by its creator, so it can do no more
than its creator could do by hand.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity
from api.services.missions import Mission, MissionError, MissionStore
from api.services.repository_validation import (
    InvalidRepositoryError,
    parse_repository,
    validate_branch,
)
from api.services.settings import effective_settings
from api.turns.tasks import launch_mission
from specifications.interfaces.agent import PERMISSION_MODES

router = APIRouter(prefix="/v1", tags=["Missions"])

SLACK_CHANNEL_ID = r"^[CGD][A-Z0-9]{2,31}$"


class MissionBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    prompt: str = Field(..., min_length=1, max_length=20_000)
    cron: str = Field(..., description="Five-field cron, e.g. '0 9 * * 1-5'")
    timezone: str = Field(default="UTC", max_length=64)
    agent_id: str | None = None
    git_repo: str | None = Field(default=None, description="owner/name or https GitHub URL")
    git_branch: str | None = None
    permission_mode: str | None = None
    slack_channel: str | None = Field(
        default=None,
        pattern=SLACK_CHANNEL_ID,
        description="Slack channel id (C..., G... or D...) to post each run's result to",
    )
    enabled: bool = True


class MissionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    cron: str | None = None
    timezone: str | None = None
    enabled: bool | None = None
    permission_mode: str | None = None
    slack_channel: str | None = Field(default=None, pattern=SLACK_CHANNEL_ID)


class MissionResponse(BaseModel):
    id: str
    user_id: str
    name: str
    prompt: str
    cron: str
    timezone: str
    agent_id: str | None
    git_repo: str | None
    git_branch: str | None
    permission_mode: str | None
    slack_channel: str | None
    enabled: bool
    next_run_at: int | None
    last_run_at: int | None
    last_session_id: str | None
    last_status: str | None


def _store(request: Request) -> MissionStore:
    store = getattr(request.app.state, "mission_store", None)
    if store is None:
        store = MissionStore(getattr(request.app.state, "db_manager", None))
        request.app.state.mission_store = store
    return store


def _view(mission: Mission) -> MissionResponse:
    return MissionResponse(
        **{k: v for k, v in vars(mission).items() if k in MissionResponse.model_fields}
    )


def _check_mode(mode: str | None) -> None:
    if mode is not None and mode not in PERMISSION_MODES:
        raise HTTPException(
            status_code=422, detail=f"permission_mode must be one of: {', '.join(PERMISSION_MODES)}"
        )


async def _check_slack_channel(request: Request, org_id: str, channel: str | None) -> None:
    """A mission may only report to a channel the organization lets the assistant post in."""
    if not channel:
        return
    conf = await effective_settings(getattr(request.app.state, "db_manager", None), org_id, "slack")
    allowed = conf.get("outbound_channels") or []
    if allowed and channel not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Channel {channel} is not in the organization's Slack outbound channels.",
        )


async def _owned(request: Request, mission_id: str) -> tuple[Mission, MissionStore]:
    org_id, user_id, roles = _get_request_identity(request)
    store = _store(request)
    mission = await store.get(org_id, mission_id)
    is_admin = any(role in ("admin", "owner") for role in roles)
    # Another member's mission is reported as absent, not forbidden.
    if mission is None or (mission.user_id != user_id and not is_admin):
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission, store


@router.get("/missions", response_model=list[MissionResponse])
async def list_missions(request: Request, all: bool = False) -> list[MissionResponse]:
    org_id, user_id, roles = _get_request_identity(request)
    see_all = all and any(role in ("admin", "owner") for role in roles)
    found = await _store(request).list(org_id, None if see_all else user_id)
    return [_view(m) for m in found]


@router.post("/missions", response_model=MissionResponse)
async def create_mission(body: MissionBody, request: Request) -> MissionResponse:
    org_id, user_id, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(
            status_code=403, detail="Forbidden: Read-only role cannot create missions"
        )
    _check_mode(body.permission_mode)
    await _check_slack_channel(request, org_id, body.slack_channel)
    try:
        repo = parse_repository(body.git_repo).url if body.git_repo else None
        branch = validate_branch(body.git_branch) if body.git_branch else None
        mission = await _store(request).create(
            Mission(
                id="",
                org_id=org_id,
                user_id=user_id,
                name=body.name,
                prompt=body.prompt,
                cron=body.cron,
                timezone=body.timezone,
                agent_id=body.agent_id,
                git_repo=repo,
                git_branch=branch,
                permission_mode=body.permission_mode,
                slack_channel=body.slack_channel,
                enabled=body.enabled,
            )
        )
    except (MissionError, InvalidRepositoryError) as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return _view(mission)


@router.patch("/missions/{mission_id}", response_model=MissionResponse)
async def update_mission(mission_id: str, body: MissionPatch, request: Request) -> MissionResponse:
    mission, store = await _owned(request, mission_id)
    _check_mode(body.permission_mode)
    changes: dict[str, Any] = body.model_dump(exclude_unset=True)
    await _check_slack_channel(request, mission.org_id, changes.get("slack_channel"))
    try:
        updated = await store.update(mission.org_id, mission.id, **changes)
    except MissionError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    assert updated is not None
    return _view(updated)


@router.delete("/missions/{mission_id}")
async def delete_mission(mission_id: str, request: Request) -> dict[str, str]:
    mission, store = await _owned(request, mission_id)
    await store.delete(mission.org_id, mission.id)
    return {"status": "deleted"}


@router.post("/missions/{mission_id}/run")
async def run_mission_now(mission_id: str, request: Request) -> dict[str, str]:
    """Start a run immediately, without moving the schedule."""
    mission, store = await _owned(request, mission_id)
    session_id = await launch_mission(request.app, mission)
    await store.mark_run(mission.org_id, mission.id, session_id, "started")
    return {"status": "started", "session_id": session_id}
