"""Missions: agent runs the platform starts on its own, and their runs.

A mission is owned like any shared object: whoever may see its owner sees it, whoever may manage
its owner changes, runs or deletes it, and changing its owner moves it (managing both sides).
Its agent must be one its runs may use (its owner's for a personal mission, a member of its
scope's for a shared one). Secrets (the mission's inbound webhook, a webhook delivery's signing
secret) are shown once, when made, and are sealed afterwards.

``POST /v1/webhooks/missions/{org}/{id}`` is the mission's own inbound webhook: public, verified
by its signature (the outbound webhooks' scheme), and only for a mission with a ``webhook``
trigger. The organization is in the path (it is not a secret) so the mission is read under its
row-level security, like any tenant's row.
"""

from __future__ import annotations

import hmac
import json
import secrets as token_source
import time
from dataclasses import asdict, replace
from typing import Any

from config_engine.crypto import CredentialCipher
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity
from api.auth.roles import can_write
from api.db.session import current_tenant_org_id
from api.services.agent_access import AgentAccessDenied, may_use, mission_principal
from api.services.mission_delivery import mission_store
from api.services.mission_records import RunStore
from api.services.mission_runs import RunOutcome, start_run
from api.services.missions import MissionError, check_webhook_urls
from api.services.outbound_webhooks import sign
from api.services.ownership import (
    OwnershipError,
    manageable,
    owner_for_write,
    owner_of,
    request_viewer,
    visible,
)
from api.services.personas import get_persona
from api.services.repository_validation import (
    InvalidRepositoryError,
    parse_repository,
    validate_branch,
)
from api.services.settings import effective_settings
from api.services.slack_bots import SlackBotStore
from api.services.starter_content import MISSION_PRESETS
from specifications.interfaces.agent import PERMISSION_MODES
from specifications.interfaces.automation import (
    DELIVERY_KINDS,
    Delivery,
    DeliveryKind,
    Mission,
    MissionTrigger,
    TriggerEvent,
    TriggerFilter,
)
from specifications.interfaces.ownership import Viewer

router = APIRouter(prefix="/v1", tags=["Missions"])

_cipher = CredentialCipher()
# An inbound call older (or newer) than this is refused: a captured one cannot be replayed later.
SIGNATURE_TOLERANCE_SECONDS = 300
MAX_INBOUND_BODY_BYTES = 64_000


class TriggerFilterBody(BaseModel):
    repositories: list[str] = Field(default_factory=list, max_length=50)
    labels: list[str] = Field(default_factory=list, max_length=50)
    branches: list[str] = Field(default_factory=list, max_length=50)


class TriggerBody(BaseModel):
    source: str = Field(..., max_length=40)
    filter: TriggerFilterBody = Field(default_factory=TriggerFilterBody)
    schedule: str = Field(default="", max_length=120, description="Five-field cron (schedule)")
    timezone: str = Field(default="UTC", max_length=64)


class DeliveryBody(BaseModel):
    kind: str = Field(..., description="session | chat | webhook | github_review | github_comment")
    bot_id: str = Field(default="", max_length=64)
    channel: str = Field(default="", max_length=64, description="Slack channel id (chat)")
    url: str = Field(default="", max_length=2000, description="https URL (webhook)")
    output_schema: dict[str, Any] = Field(
        default_factory=dict, description="JSON Schema the webhook's result follows"
    )


class MissionBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=20_000)
    scope: str = Field(default="user", description="Owner: 'user', 'team' or 'org'")
    team_id: str | None = None
    triggers: list[TriggerBody] = Field(default_factory=list, max_length=3)
    deliveries: list[DeliveryBody] = Field(default_factory=list, max_length=4)
    agent_id: str = Field(default="", max_length=64)
    permission_mode: str = Field(default="", max_length=16)
    model_tier: str = Field(default="", max_length=16)
    sandbox_type: str = Field(default="", max_length=64)
    mode: str = Field(default="work", max_length=16)
    auto_fix: bool = False
    git_repo: str = Field(default="", max_length=255)
    git_branch: str = Field(default="", max_length=255)
    enabled: bool = True


class MissionPatch(BaseModel):
    enabled: bool


class MissionView(BaseModel):
    id: str
    name: str
    instructions: str
    scope: str
    user_id: str | None
    team_id: str | None
    triggers: list[TriggerBody]
    deliveries: list[DeliveryBody]
    agent_id: str
    permission_mode: str
    model_tier: str
    sandbox_type: str
    mode: str
    auto_fix: bool
    git_repo: str
    git_branch: str
    enabled: bool
    run_as: str
    next_run_at: int | None
    last_run_at: int | None
    last_session_id: str | None
    last_status: str | None
    created_at: int
    updated_at: int
    can_manage: bool = False
    # The inbound webhook's path (``webhook`` trigger) and whether the delivery is signed.
    inbound_path: str | None = None
    has_inbound_secret: bool = False
    has_webhook_secret: bool = False


class CreatedMissionView(MissionView):
    # Shown once: the inbound webhook's secret, and the webhook delivery's signing secret.
    inbound_secret: str | None = None
    webhook_secret: str | None = None


class RunBody(BaseModel):
    text: str = Field(default="", max_length=8000, description="What this run is about")


class RunResult(BaseModel):
    status: str
    session_id: str = ""
    run_id: str = ""
    reason: str = ""


class RunView(BaseModel):
    id: str
    session_id: str
    source: str
    title: str
    status: str
    deliveries: list[dict[str, str]]
    started_at: int
    finished_at: int | None


class PresetView(BaseModel):
    id: str
    name: str
    description: str
    mission: MissionBody


class RotatedSecret(BaseModel):
    which: str
    secret: str


# -- conversions -----------------------------------------------------------------------------


def _mission_from(body: MissionBody, org_id: str, base: Mission | None) -> Mission:
    triggers = tuple(
        MissionTrigger(
            source=t.source,
            filter=TriggerFilter(
                repositories=tuple(t.filter.repositories),
                labels=tuple(t.filter.labels),
                branches=tuple(t.filter.branches),
            ),
            schedule=t.schedule.strip(),
            timezone=t.timezone or "UTC",
        )
        for t in body.triggers
    )
    deliveries = tuple(
        Delivery(
            kind=_kind(d.kind),
            bot_id=d.bot_id,
            channel=d.channel,
            url=d.url.strip(),
            output_schema=dict(d.output_schema),
        )
        for d in body.deliveries
    )
    base = base or Mission(id="", org_id=org_id, name="", instructions="")
    return replace(
        base,
        name=body.name,
        instructions=body.instructions,
        triggers=triggers,
        deliveries=deliveries or (Delivery("session"),),
        agent_id=body.agent_id,
        permission_mode=body.permission_mode,
        model_tier=body.model_tier,
        sandbox_type=body.sandbox_type,
        mode=body.mode,
        auto_fix=body.auto_fix,
        git_repo=body.git_repo,
        git_branch=body.git_branch,
        enabled=body.enabled,
    )


def _kind(value: str) -> DeliveryKind:
    for kind in DELIVERY_KINDS:
        if kind == value:
            return kind
    raise HTTPException(status_code=422, detail=f"Unknown delivery '{value}'.")


def _view(mission: Mission, viewer: Viewer, secrets: dict[str, str]) -> MissionView:
    inbound = any(t.source == "webhook" for t in mission.triggers)
    return MissionView(
        **{
            name: getattr(mission, name)
            for name in MissionView.model_fields
            if hasattr(mission, name) and name not in ("triggers", "deliveries")
        },
        triggers=[
            TriggerBody(
                source=t.source,
                filter=TriggerFilterBody(**{k: list(v) for k, v in asdict(t.filter).items()}),
                schedule=t.schedule,
                timezone=t.timezone,
            )
            for t in mission.triggers
        ],
        deliveries=[DeliveryBody(**asdict(d)) for d in mission.deliveries],
        can_manage=manageable(mission, viewer),
        inbound_path=f"/v1/webhooks/missions/{mission.org_id}/{mission.id}" if inbound else None,
        has_inbound_secret=bool(secrets.get("inbound")),
        has_webhook_secret=bool(secrets.get("webhook")),
    )


# -- checks ----------------------------------------------------------------------------------


def _check_fields(body: MissionBody) -> None:
    if body.permission_mode and body.permission_mode not in PERMISSION_MODES:
        raise HTTPException(
            status_code=422, detail=f"permission_mode must be one of: {', '.join(PERMISSION_MODES)}"
        )
    try:
        if body.git_repo:
            body.git_repo = parse_repository(body.git_repo).url
        if body.git_branch:
            body.git_branch = validate_branch(body.git_branch)
    except InvalidRepositoryError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


async def _check_channels(request: Request, mission: Mission) -> None:
    """A chat delivery may only post in a channel the organization lets the assistant post in."""
    channels = [d.channel for d in mission.deliveries if d.kind == "chat"]
    if not channels:
        return
    conf = await effective_settings(
        getattr(request.app.state, "db_manager", None), mission.org_id, "slack"
    )
    allowed = conf.get("outbound_channels") or []
    refused = [c for c in channels if allowed and c not in allowed]
    if refused:
        raise HTTPException(
            status_code=422,
            detail=f"Channel {refused[0]} is not in the organization's Slack outbound channels.",
        )


async def _check_agent(
    request: Request, mission: Mission, viewer: Viewer, roles: list[str]
) -> None:
    """The person saving may use the agent, and so may the mission's runs."""
    if not mission.agent_id:
        return
    db_mgr = getattr(request.app.state, "db_manager", None)
    try:
        agent = await get_persona(
            db_mgr, mission.org_id, viewer.user_id, mission.agent_id, roles=roles
        )
    except AgentAccessDenied as err:
        raise HTTPException(status_code=403, detail=str(err)) from err
    if agent is None:
        raise HTTPException(status_code=422, detail="That agent does not exist.")
    runs_as = await mission_principal(
        db_mgr, mission.org_id, mission.scope, mission.user_id, mission.team_id, mission.run_as
    )
    if not await may_use(db_mgr, mission.org_id, runs_as, agent):
        raise HTTPException(
            status_code=422,
            detail=f'The mission\'s runs may not use "{agent.name}": grant it to its scope first.',
        )


def _new_secrets(mission: Mission, kept: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """(sealed secrets to store, plain ones to show once): an inbound secret for a webhook
    trigger and a signing secret for a webhook delivery, each made the first time it is needed."""
    sealed, shown = dict(kept), {}
    needs = {
        "inbound": any(t.source == "webhook" for t in mission.triggers),
        "webhook": any(d.kind == "webhook" for d in mission.deliveries),
    }
    for which, needed in needs.items():
        if needed and which not in sealed:
            plain = f"rcm_{token_source.token_urlsafe(32)}"
            sealed[which], shown[which] = _cipher.encrypt(plain), plain
        if not needed:
            sealed.pop(which, None)
    return sealed, shown


async def _managed(request: Request, mission_id: str) -> tuple[str, Viewer, Mission]:
    org_id, viewer = await request_viewer(request)
    mission = await mission_store(request.app).get(org_id, mission_id)
    if mission is None or not (visible(mission, viewer) or manageable(mission, viewer)):
        raise HTTPException(status_code=404, detail="Mission not found")
    if not manageable(mission, viewer):
        raise HTTPException(
            status_code=403, detail="Only those who manage this mission can do this."
        )
    return org_id, viewer, mission


# -- routes ----------------------------------------------------------------------------------


@router.get("/missions/presets", response_model=list[PresetView])
async def list_presets() -> list[PresetView]:
    """Starting points for usual missions (pull request review, CI fixes, digests, triage)."""
    return [
        PresetView(
            id=p.id, name=p.name, description=p.description, mission=MissionBody(**p.mission)
        )
        for p in MISSION_PRESETS
    ]


class ChatBotView(BaseModel):
    id: str
    name: str


@router.get("/missions/chat-bots", response_model=list[ChatBotView])
async def list_chat_bots(request: Request) -> list[ChatBotView]:
    """The organization's enabled Slack bots a chat delivery may name (ids and names only)."""
    org_id, _, _ = _get_request_identity(request)
    store = getattr(request.app.state, "slack_bot_store", None)
    if store is None:
        store = SlackBotStore(getattr(request.app.state, "db_manager", None))
    return [ChatBotView(id=b.id, name=b.name) for b in await store.list(org_id) if b.enabled]


@router.get("/missions", response_model=list[MissionView])
async def list_missions(request: Request) -> list[MissionView]:
    """The missions the caller may see: theirs, their teams' and the organization's."""
    org_id, viewer = await request_viewer(request)
    store = mission_store(request.app)
    return [
        _view(m, viewer, await store.secrets(org_id, m.id))
        for m in await store.all_for(org_id, viewer)
    ]


@router.get("/missions/{mission_id}", response_model=MissionView)
async def get_mission(mission_id: str, request: Request) -> MissionView:
    org_id, viewer = await request_viewer(request)
    store = mission_store(request.app)
    mission = await store.get(org_id, mission_id)
    if mission is None or not visible(mission, viewer):
        raise HTTPException(status_code=404, detail="Mission not found")
    return _view(mission, viewer, await store.secrets(org_id, mission_id))


@router.post("/missions", response_model=CreatedMissionView)
async def create_mission(body: MissionBody, request: Request) -> CreatedMissionView:
    """Create a mission for the caller, one of their teams or the organization."""
    org_id, user_id, roles = _get_request_identity(request)
    if not can_write(roles):
        raise HTTPException(status_code=403, detail="Forbidden: viewers cannot create missions")
    _, viewer = await request_viewer(request)
    _check_fields(body)
    mission = _mission_from(body, org_id, None)
    try:
        owner = await owner_for_write(
            getattr(request.app.state, "db_manager", None), org_id, viewer,
            scope=body.scope, team_id=body.team_id, current=None, what="missions",
        )  # fmt: skip
    except OwnershipError as err:
        raise err.as_http() from err
    mission = replace(
        mission, scope=owner.scope, user_id=owner.user_id, team_id=owner.team_id,
        created_by=user_id, run_as=owner.user_id or "",
    )  # fmt: skip
    await _check_agent(request, mission, viewer, roles)
    await _check_channels(request, mission)
    sealed, shown = _new_secrets(mission, {})
    try:
        await check_webhook_urls(mission)
        created = await mission_store(request.app).create(mission, sealed)
    except MissionError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    view = _view(created, viewer, sealed)
    return CreatedMissionView(
        **view.model_dump(),
        inbound_secret=shown.get("inbound"),
        webhook_secret=shown.get("webhook"),
    )


@router.put("/missions/{mission_id}", response_model=CreatedMissionView)
async def replace_mission(
    mission_id: str, body: MissionBody, request: Request
) -> CreatedMissionView:
    """Replace a mission; naming another owner moves it (managing both sides)."""
    org_id, viewer, current = await _managed(request, mission_id)
    _, _, roles = _get_request_identity(request)
    _check_fields(body)
    try:
        owner = await owner_for_write(
            getattr(request.app.state, "db_manager", None), org_id, viewer,
            scope=body.scope, team_id=body.team_id, current=owner_of(current), what="missions",
        )  # fmt: skip
    except OwnershipError as err:
        raise err.as_http() from err
    moved = (owner.scope, owner.team_id, owner.user_id) != (
        current.scope,
        current.team_id,
        current.user_id,
    )
    mission = replace(
        _mission_from(body, org_id, current),
        scope=owner.scope, user_id=owner.user_id, team_id=owner.team_id,
        # A shared mission's service member is its own; one moved to a person runs as them.
        run_as=owner.user_id or ("" if moved else current.run_as),
    )  # fmt: skip
    await _check_agent(request, mission, viewer, roles)
    await _check_channels(request, mission)
    store = mission_store(request.app)
    sealed, shown = _new_secrets(mission, await store.secrets(org_id, mission_id))
    try:
        await check_webhook_urls(mission)
        saved = await store.save(mission, sealed)
    except MissionError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    view = _view(saved, viewer, sealed)
    return CreatedMissionView(
        **view.model_dump(),
        inbound_secret=shown.get("inbound"),
        webhook_secret=shown.get("webhook"),
    )


@router.patch("/missions/{mission_id}", response_model=MissionView)
async def toggle_mission(mission_id: str, body: MissionPatch, request: Request) -> MissionView:
    """Switch a mission on or off."""
    org_id, viewer, mission = await _managed(request, mission_id)
    store = mission_store(request.app)
    try:
        saved = await store.save(replace(mission, enabled=body.enabled))
    except MissionError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return _view(saved, viewer, await store.secrets(org_id, mission_id))


@router.delete("/missions/{mission_id}")
async def delete_mission(mission_id: str, request: Request) -> dict[str, str]:
    org_id, _, mission = await _managed(request, mission_id)
    await mission_store(request.app).delete(org_id, mission.id)
    return {"status": "deleted"}


@router.post("/missions/{mission_id}/secrets/{which}", response_model=RotatedSecret)
async def rotate_secret(mission_id: str, which: str, request: Request) -> RotatedSecret:
    """Replace the inbound webhook's secret (``inbound``) or the webhook delivery's signing
    secret (``webhook``); the new one is shown once."""
    org_id, _, mission = await _managed(request, mission_id)
    store = mission_store(request.app)
    kept = await store.secrets(org_id, mission_id)
    if which not in kept:
        raise HTTPException(status_code=404, detail="This mission has no such secret.")
    plain = f"rcm_{token_source.token_urlsafe(32)}"
    await store.save(mission, {**kept, which: _cipher.encrypt(plain)})
    return RotatedSecret(which=which, secret=plain)


@router.post("/missions/{mission_id}/run", response_model=RunResult)
async def run_mission_now(mission_id: str, body: RunBody, request: Request) -> RunResult:
    """Start a run now, without moving the schedule (``text``: what this run is about)."""
    _, viewer, mission = await _managed(request, mission_id)
    event = TriggerEvent(
        source="api",
        title=mission.name,
        body=body.text or f"Run of '{mission.name}' started by hand.",
        actor=viewer.user_id,
        trusted=True,
    )
    outcome = await start_run(request.app, mission, event)
    return _run_result(outcome)


def _run_result(outcome: RunOutcome) -> RunResult:
    return RunResult(
        status=outcome.status,
        session_id=outcome.session_id,
        run_id=outcome.run_id,
        reason=outcome.reason,
    )


@router.get("/missions/{mission_id}/runs", response_model=list[RunView])
async def list_runs(mission_id: str, request: Request) -> list[RunView]:
    """The mission's latest runs, newest first, with how each delivery went."""
    org_id, viewer = await request_viewer(request)
    mission = await mission_store(request.app).get(org_id, mission_id)
    if mission is None or not visible(mission, viewer):
        raise HTTPException(status_code=404, detail="Mission not found")
    runs = await RunStore(getattr(request.app.state, "db_manager", None)).of_mission(
        org_id, mission_id
    )
    return [
        RunView(
            id=r.id, session_id=r.session_id, source=r.source, title=r.title, status=r.status,
            deliveries=[dict(d) for d in r.deliveries], started_at=r.started_at,
            finished_at=r.finished_at,
        )
        for r in runs
    ]  # fmt: skip


@router.post("/webhooks/missions/{org_id}/{mission_id}", response_model=RunResult)
async def inbound_webhook(
    org_id: str,
    mission_id: str,
    request: Request,
    x_rocket_timestamp: str = Header(default="", alias="X-Rocket-Timestamp"),
    x_rocket_signature: str = Header(default="", alias="X-Rocket-Signature"),
) -> RunResult:
    """A mission's own webhook: ``X-Rocket-Signature: sha256=<hex HMAC of "<timestamp>.<body>">``
    with the mission's inbound secret and ``X-Rocket-Timestamp`` within five minutes. The JSON
    body is what the run is about (capped)."""
    raw = await request.body()
    if len(raw) > MAX_INBOUND_BODY_BYTES:
        raise HTTPException(status_code=413, detail="The body is over 64 KB.")
    store = mission_store(request.app)
    token = current_tenant_org_id.set(org_id)
    try:
        mission = await store.get(org_id, mission_id)
        if mission is None or not any(t.source == "webhook" for t in mission.triggers):
            raise HTTPException(status_code=404, detail="Mission not found")
        sealed = (await store.secrets(org_id, mission_id)).get("inbound")
    finally:
        current_tenant_org_id.reset(token)
    if not sealed or not _signed(
        _cipher.decrypt(sealed), x_rocket_timestamp, raw, x_rocket_signature
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired signature")
    if not mission.enabled:
        return RunResult(status="ignored", reason="The mission is switched off.")
    try:
        text = json.dumps(json.loads(raw or b"{}"), indent=2)[:8000]
    except ValueError as err:
        raise HTTPException(status_code=422, detail="The body is not JSON.") from err
    event = TriggerEvent(
        source="webhook",
        delivery_id=x_rocket_signature[-32:],
        title=mission.name,
        body=f"The mission's webhook was called with:\n```json\n{text}\n```",
        actor="webhook",
        trusted=True,
    )
    return _run_result(await start_run(request.app, mission, event))


def _signed(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    try:
        moment = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - moment) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    return hmac.compare_digest(sign(secret, moment, body), signature)
