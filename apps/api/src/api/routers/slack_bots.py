"""Slack bots administration: listing, creation, editing, testing, and router decisions.

Every bot represents an independent Slack app with its own tokens, agent or router,
response mode, and policy overrides. Tokens enter write-only and are sealed with
CredentialCipher. Rows are protected by PostgreSQL row-level security.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.auth.access import _require_admin
from api.services.agent_access import AgentAccessDenied, require_usable_agent
from api.services.slack_bot_setup import check_tokens, manifest
from api.services.slack_bots import (
    SlackBotError,
    SlackBotRecord,
    SlackBotStore,
    SlackIdentity,
    SlackTokens,
    runtime_bot,
    slash_command_of,
)
from api.services.slack_link import bind_workspace
from api.services.slack_routing import list_route_decisions, try_classify
from specifications.interfaces.llm import LLMGatewayProtocol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin/slack", tags=["Slack"])


class SlackBotResponse(BaseModel):
    """Cockpit view of a Slack bot, tokens omitted."""

    id: str
    org_id: str
    name: str
    enabled: bool = True
    bot_token_fingerprint: str | None = None
    app_token_fingerprint: str | None = None
    team_id: str | None = None
    team_name: str | None = None
    bot_user_id: str | None = None
    app_id: str | None = None
    handle: str | None = None
    slash_command: str | None = Field(None, description="The command the bot chose, if any")
    command: str = Field("/rocket", description="The slash command the bot answers")
    agent_id: str | None = None
    response_mode: str = "thread"
    respond_in_dms: bool = True
    listen_channels: list[str] = Field(default_factory=list)
    private_reaction: str = "white_check_mark"
    private_dm_result: bool = False
    private_dm_updates: bool = Field(
        False,
        description="Private requests: send questions, approvals, errors and the result by "
        "direct message, with a link to the thread",
    )
    router: dict[str, Any] | None = None
    policy: dict[str, Any] = Field(default_factory=dict)
    created_at: int
    updated_at: int


class SlackBotCreateRequest(BaseModel):
    """Payload to register a new Slack bot."""

    name: str = Field(..., min_length=1, max_length=100)
    bot_token: str = Field(..., description="Slack bot token starting with xoxb-")
    app_token: str = Field(..., description="Slack app-level token starting with xapp-")
    slash_command: str | None = Field(None, description="Empty: named after the bot's handle")
    agent_id: str | None = None
    response_mode: str = "thread"
    respond_in_dms: bool = True
    listen_channels: list[str] = Field(default_factory=list)
    private_reaction: str = "white_check_mark"
    private_dm_result: bool = False
    private_dm_updates: bool = Field(
        False,
        description="Private requests: send questions, approvals, errors and the result by "
        "direct message, with a link to the thread",
    )
    router: dict[str, Any] | None = None
    policy: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class SlackBotUpdateRequest(BaseModel):
    """Payload to update an existing Slack bot."""

    name: str | None = None
    bot_token: str | None = None
    app_token: str | None = None
    slash_command: str | None = None
    agent_id: str | None = None
    response_mode: str | None = None
    respond_in_dms: bool | None = None
    listen_channels: list[str] | None = None
    private_reaction: str | None = None
    private_dm_result: bool | None = None
    private_dm_updates: bool | None = None
    router: dict[str, Any] | None = None
    policy: dict[str, Any] | None = None
    enabled: bool | None = None


class RouterTryRequest(BaseModel):
    """Sample message to classify using the bot's router configuration."""

    text: str = Field(..., min_length=1)
    thread_excerpt: str = ""


class RouterTryResponse(BaseModel):
    """Outcome of a test classification."""

    route: str
    applied: str
    confidence: float
    reason: str
    model: str
    latency_ms: int
    probabilities: dict[str, float] = Field(default_factory=dict)


class BotTestResponse(BaseModel):
    """Result of testing a bot's Slack credentials."""

    ok: bool
    team_id: str
    team_name: str
    bot_user_id: str
    app_id: str
    handle: str


def _get_store(request: Request) -> SlackBotStore:
    store = getattr(request.app.state, "slack_bot_store", None)
    if store is None:
        store = SlackBotStore(getattr(request.app.state, "db_manager", None))
        request.app.state.slack_bot_store = store
    return store


async def _claim_workspace(
    request: Request, org_id: str, admin_id: str, identity: SlackIdentity
) -> None:
    """A bot belongs to its organization's workspace: the first bot binds it, and a workspace
    another organization owns is refused (its people and guests are that organization's)."""
    db_mgr = getattr(request.app.state, "db_manager", None)
    try:
        await bind_workspace(db_mgr, identity.team_id, org_id, admin_id)
    except ValueError as err:
        raise HTTPException(
            status_code=403,
            detail="This Slack workspace belongs to another organization in Rocket Chat",
        ) from err


def _as_response(record: SlackBotRecord) -> SlackBotResponse:
    return SlackBotResponse(
        id=record.id,
        org_id=record.org_id,
        name=record.name,
        enabled=record.enabled,
        bot_token_fingerprint=record.bot_token_fingerprint,
        app_token_fingerprint=record.app_token_fingerprint,
        team_id=record.team_id,
        team_name=record.team_name,
        bot_user_id=record.bot_user_id,
        app_id=record.app_id,
        handle=record.handle,
        slash_command=record.slash_command,
        command=slash_command_of(record),
        agent_id=record.agent_id,
        response_mode=record.response_mode,
        respond_in_dms=record.respond_in_dms,
        listen_channels=record.listen_channels,
        private_reaction=record.private_reaction,
        private_dm_result=record.private_dm_result,
        private_dm_updates=record.private_dm_updates,
        router=record.router,
        policy=record.policy,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


async def _apply_to_running_bots(request: Request) -> None:
    """Start, restart or stop bots on this replica now; the others follow at their resync."""
    manager = getattr(request.app.state, "slack_bot_manager", None)
    if manager is not None:
        try:
            await manager.sync()
        except Exception as err:
            logger.warning("Could not apply the Slack bot change: %s", err)


def _default_model(request: Request) -> str:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    return str(getattr(orchestrator, "default_model", "") or "")


@router.get("/bots", response_model=list[SlackBotResponse])
async def list_bots(request: Request) -> list[SlackBotResponse]:
    """List all configured Slack bots for the caller's organization."""
    org_id, _, _ = _require_admin(request)
    store = _get_store(request)
    records = await store.list(org_id)
    return [_as_response(r) for r in records]


@router.post("/bots", response_model=SlackBotResponse, status_code=201)
async def create_bot(body: SlackBotCreateRequest, request: Request) -> SlackBotResponse:
    """Register a new Slack bot with its encrypted tokens and identity."""
    org_id, admin_id, _ = _require_admin(request)
    store = _get_store(request)
    tokens = SlackTokens(body.bot_token.strip(), body.app_token.strip())
    try:
        identity = await check_tokens(tokens)
    except SlackBotError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    await _claim_workspace(request, org_id, admin_id, identity)
    await _bot_agents_serve_guests(request, org_id, admin_id, body.agent_id, body.policy)
    record = SlackBotRecord(
        id="",
        org_id=org_id,
        name=body.name.strip(),
        enabled=body.enabled,
        team_id=identity.team_id,
        team_name=identity.team_name,
        bot_user_id=identity.bot_user_id,
        app_id=identity.app_id,
        handle=identity.handle,
        slash_command=body.slash_command,
        agent_id=body.agent_id,
        response_mode=body.response_mode,
        respond_in_dms=body.respond_in_dms,
        listen_channels=body.listen_channels,
        private_reaction=body.private_reaction,
        private_dm_result=body.private_dm_result,
        private_dm_updates=body.private_dm_updates,
        router=body.router,
        policy=body.policy,
        created_by=admin_id,
    )
    try:
        created = await store.create(record, tokens)
    except SlackBotError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    await _apply_to_running_bots(request)
    return _as_response(created)


@router.get("/bots/{bot_id}", response_model=SlackBotResponse)
async def get_bot(bot_id: str, request: Request) -> SlackBotResponse:
    """Get details of a single Slack bot (tokens omitted)."""
    org_id, _, _ = _require_admin(request)
    record = await _get_store(request).get(org_id, bot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Slack bot not found")
    return _as_response(record)


@router.put("/bots/{bot_id}", response_model=SlackBotResponse)
async def update_bot(
    bot_id: str, body: SlackBotUpdateRequest, request: Request
) -> SlackBotResponse:
    """Update a Slack bot's configuration or rotate its tokens."""
    org_id, admin_id, _ = _require_admin(request)
    store = _get_store(request)
    changes = body.model_dump(exclude_unset=True, exclude={"bot_token", "app_token"})

    tokens: SlackTokens | None = None
    identity = None
    if body.bot_token or body.app_token:
        if not (body.bot_token and body.app_token):
            raise HTTPException(
                status_code=400, detail="Both bot_token and app_token are required to update tokens"
            )
        tokens = SlackTokens(body.bot_token.strip(), body.app_token.strip())
        try:
            identity = await check_tokens(tokens)
        except SlackBotError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        await _claim_workspace(request, org_id, admin_id, identity)

    await _bot_agents_serve_guests(
        request, org_id, admin_id, changes.get("agent_id"), changes.get("policy") or {}
    )
    try:
        updated = await store.update(org_id, bot_id, changes, tokens=tokens, identity=identity)
    except SlackBotError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    if updated is None:
        raise HTTPException(status_code=404, detail="Slack bot not found")

    await _apply_to_running_bots(request)
    return _as_response(updated)


@router.delete("/bots/{bot_id}")
async def delete_bot(bot_id: str, request: Request) -> dict[str, bool]:
    """Remove a Slack bot and stop its background connection."""
    org_id, _, _ = _require_admin(request)
    deleted = await _get_store(request).delete(org_id, bot_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Slack bot not found")
    await _apply_to_running_bots(request)
    return {"deleted": True}


@router.post("/bots/{bot_id}/test", response_model=BotTestResponse)
async def test_bot_connection(bot_id: str, request: Request) -> BotTestResponse:
    """Validate a bot's stored tokens with Slack, and refresh what Slack says about the bot
    (its display name changes when the app's manifest does)."""
    org_id, _, _ = _require_admin(request)
    store = _get_store(request)
    tokens = await store.tokens(org_id, bot_id)
    if tokens is None:
        raise HTTPException(status_code=400, detail="Bot tokens are not configured")
    try:
        identity = await check_tokens(tokens)
    except SlackBotError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    await store.update(org_id, bot_id, {}, identity=identity)
    await _apply_to_running_bots(request)
    return BotTestResponse(
        ok=True,
        team_id=identity.team_id,
        team_name=identity.team_name,
        bot_user_id=identity.bot_user_id,
        app_id=identity.app_id,
        handle=identity.handle,
    )


@router.get("/bots/{bot_id}/manifest")
async def get_bot_manifest(bot_id: str, request: Request) -> dict[str, Any]:
    """Generate the Slack app manifest JSON for this bot based on its configuration."""
    org_id, _, _ = _require_admin(request)
    record = await _get_store(request).get(org_id, bot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Slack bot not found")
    web_url = os.getenv("ROCKET_PUBLIC_URL", str(request.base_url).rstrip("/"))
    return manifest(record, web_url=web_url)


@router.post("/bots/{bot_id}/router/try", response_model=RouterTryResponse)
async def try_router(bot_id: str, body: RouterTryRequest, request: Request) -> RouterTryResponse:
    """Classify a sample message with the bot's router without storing a decision."""
    org_id, _, _ = _require_admin(request)
    record = await _get_store(request).get(org_id, bot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Slack bot not found")
    if record.router is None:
        raise HTTPException(status_code=400, detail="This bot does not have a router configured")

    gateway: LLMGatewayProtocol | None = getattr(request.app.state, "llm_gateway", None)
    if gateway is None:
        raise HTTPException(status_code=503, detail="LLM gateway not ready")

    bot = runtime_bot(record, SlackTokens("", ""))
    db_mgr = getattr(request.app.state, "db_manager", None)
    try:
        result = await try_classify(
            gateway,
            bot,
            org_id,
            body.text,
            thread_excerpt=body.thread_excerpt,
            db_mgr=db_mgr,
            default_model=_default_model(request),
        )
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Classification failed: {err}") from err

    return RouterTryResponse(**result)


@router.get("/bots/{bot_id}/decisions")
async def get_bot_decisions(
    bot_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """List recent routing decisions recorded for this bot."""
    org_id, _, _ = _require_admin(request)
    record = await _get_store(request).get(org_id, bot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Slack bot not found")
    db_mgr = getattr(request.app.state, "db_manager", None)
    return await list_route_decisions(db_mgr, org_id, bot_id, limit=limit)


async def _bot_agents_serve_guests(
    request: Request, org_id: str, admin_id: str, agent_id: str | None, policy: dict[str, Any]
) -> None:
    """A bot's agent, and each channel profile's, must be usable by Slack guests: the bot
    answers people who have not paired, whose turns run as guests."""
    profiles = policy.get("channel_profiles") if isinstance(policy, dict) else None
    agents = [agent_id] + [
        str(profile.get("agent_persona") or "")
        for profile in (profiles or {}).values()
        if isinstance(profile, dict)
    ]
    db_mgr = getattr(request.app.state, "db_manager", None)
    for candidate in dict.fromkeys(a for a in agents if a):
        try:
            await require_usable_agent(db_mgr, org_id, admin_id, None, candidate, for_guests=True)
        except AgentAccessDenied as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
