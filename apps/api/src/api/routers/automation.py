"""Automation: the organization's triggers and outbound webhooks (administrators), and running a
trigger of the ``api`` source from a script."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity, _require_admin
from api.services import outbound_webhooks
from api.services.audit import AuditService
from api.services.outbound_webhooks import OutboundWebhookError
from api.services.triggers import (
    TriggerError,
    claim_delivery,
    delete_trigger,
    list_triggers,
    save_trigger,
    start_from_trigger,
)
from specifications.interfaces.automation import (
    OutboundWebhook,
    Trigger,
    TriggerEvent,
    TriggerFilter,
)

router = APIRouter(prefix="/v1", tags=["Automation"])


class TriggerFilterBody(BaseModel):
    repositories: list[str] = Field(default_factory=list, max_length=50)
    labels: list[str] = Field(default_factory=list, max_length=50)
    branches: list[str] = Field(default_factory=list, max_length=50)


class TriggerBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    source: str
    filter: TriggerFilterBody = Field(default_factory=TriggerFilterBody)
    agent_id: str = Field(default="", max_length=64)
    permission_mode: str = Field(default="", max_length=16)
    model_tier: str = ""
    sandbox_type: str = Field(default="", max_length=64)
    mode: str = "work"
    auto_fix: bool = False
    instructions: str = Field(default="", max_length=4000)
    schedule: str = Field(default="", max_length=120)
    enabled: bool = True


class TriggerView(TriggerBody):
    id: str


class TriggerList(BaseModel):
    triggers: list[TriggerView]


class TriggerRunBody(BaseModel):
    """What an ``api`` trigger works on: the task, and optionally where."""

    prompt: str = Field(..., min_length=1, max_length=20000)
    repository: str = Field(default="", max_length=200)
    branch: str = Field(default="", max_length=200)
    # The caller's id for this request: retrying it starts no second session.
    idempotency_key: str = Field(default="", max_length=128)


class TriggerRunResult(BaseModel):
    status: str
    session_id: str = ""


class WebhookBody(BaseModel):
    url: str = Field(..., min_length=9, max_length=2000)
    events: list[str] = Field(..., min_length=1)
    description: str = Field(default="", max_length=255)


class WebhookView(BaseModel):
    id: str
    url: str
    events: list[str]
    description: str
    enabled: bool
    secret_fingerprint: str


class CreatedWebhookView(WebhookView):
    # Shown once: store it to check the ``X-Rocket-Signature`` of each report.
    secret: str


class WebhookList(BaseModel):
    webhooks: list[WebhookView]


def _trigger(org_id: str, trigger_id: str, body: TriggerBody) -> Trigger:
    return Trigger(
        id=trigger_id,
        org_id=org_id,
        name=body.name,
        source=body.source,
        filter=TriggerFilter(**{k: tuple(v) for k, v in body.filter.model_dump().items()}),
        agent_id=body.agent_id,
        permission_mode=body.permission_mode,
        model_tier=body.model_tier,
        sandbox_type=body.sandbox_type,
        mode=body.mode,
        auto_fix=body.auto_fix,
        instructions=body.instructions,
        schedule=body.schedule,
        enabled=body.enabled,
    )


def _trigger_view(trigger: Trigger) -> TriggerView:
    return TriggerView(
        id=trigger.id,
        name=trigger.name,
        source=trigger.source,
        filter=TriggerFilterBody(**{k: list(v) for k, v in trigger.filter.__dict__.items()}),
        agent_id=trigger.agent_id,
        permission_mode=trigger.permission_mode,
        model_tier=trigger.model_tier,
        sandbox_type=trigger.sandbox_type,
        mode=trigger.mode,
        auto_fix=trigger.auto_fix,
        instructions=trigger.instructions,
        schedule=trigger.schedule,
        enabled=trigger.enabled,
    )


async def _audit(
    request: Request, org_id: str, actor: str, event: str, details: dict[str, Any]
) -> None:
    audit = getattr(request.app.state, "audit_service", None)
    if isinstance(audit, AuditService):
        await audit.record(event, org_id=org_id, actor_user_id=actor, metadata=details)


@router.get("/admin/triggers", response_model=TriggerList)
async def get_triggers(request: Request) -> TriggerList:
    """The organization's triggers (an organization without any uses the deployment's label)."""
    org_id, _, _ = _require_admin(request)
    triggers = await list_triggers(getattr(request.app.state, "db_manager", None), org_id)
    return TriggerList(triggers=[_trigger_view(t) for t in triggers])


@router.post("/admin/triggers", response_model=TriggerView, status_code=201)
async def create_trigger(body: TriggerBody, request: Request) -> TriggerView:
    """Add a trigger."""
    org_id, user_id, _ = _require_admin(request)
    try:
        saved = await save_trigger(
            getattr(request.app.state, "db_manager", None), _trigger(org_id, "", body), user_id
        )
    except TriggerError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    await _audit(
        request,
        org_id,
        user_id,
        "trigger.created",
        {"trigger_id": saved.id, "source": saved.source},
    )
    return _trigger_view(saved)


@router.put("/admin/triggers/{trigger_id}", response_model=TriggerView)
async def update_trigger(trigger_id: str, body: TriggerBody, request: Request) -> TriggerView:
    """Replace a trigger."""
    org_id, user_id, _ = _require_admin(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    if not any(t.id == trigger_id for t in await list_triggers(db_mgr, org_id)):
        raise HTTPException(status_code=404, detail="Trigger not found")
    try:
        saved = await save_trigger(db_mgr, _trigger(org_id, trigger_id, body), user_id)
    except TriggerError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    await _audit(request, org_id, user_id, "trigger.updated", {"trigger_id": trigger_id})
    return _trigger_view(saved)


@router.delete("/admin/triggers/{trigger_id}", status_code=204)
async def remove_trigger(trigger_id: str, request: Request) -> None:
    """Delete a trigger."""
    org_id, user_id, _ = _require_admin(request)
    if not await delete_trigger(getattr(request.app.state, "db_manager", None), org_id, trigger_id):
        raise HTTPException(status_code=404, detail="Trigger not found")
    await _audit(request, org_id, user_id, "trigger.deleted", {"trigger_id": trigger_id})


@router.post("/triggers/{trigger_id}/run", response_model=TriggerRunResult)
async def run_trigger(trigger_id: str, body: TriggerRunBody, request: Request) -> TriggerRunResult:
    """Start a session from an ``api`` trigger, as the caller (an API token or a member). The
    trigger's agent, mode, tier and sandbox type apply; ``idempotency_key`` starts it once."""
    org_id, user_id, _ = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    trigger = next((t for t in await list_triggers(db_mgr, org_id) if t.id == trigger_id), None)
    if trigger is None or trigger.source != "api" or not trigger.enabled:
        raise HTTPException(status_code=404, detail="No enabled api trigger with this id")
    if body.idempotency_key and not await claim_delivery(
        db_mgr, org_id, "api", body.idempotency_key
    ):
        return TriggerRunResult(status="duplicate")
    event = TriggerEvent(
        source="api",
        delivery_id=body.idempotency_key,
        repository=body.repository.lower(),
        branch=body.branch,
        title=body.prompt.splitlines()[0][:120],
        body=body.prompt,
        key=body.idempotency_key or "",
        actor=user_id,
        trusted=True,
    )
    outcome = await start_from_trigger(request.app, trigger, event, user_id)
    return TriggerRunResult(status=outcome.status, session_id=outcome.session_id)


def _webhook_view(hook: OutboundWebhook) -> WebhookView:
    return WebhookView(
        id=hook.id,
        url=hook.url,
        events=list(hook.events),
        description=hook.description,
        enabled=hook.enabled,
        secret_fingerprint=hook.secret_fingerprint,
    )


@router.get("/admin/webhooks", response_model=WebhookList)
async def get_webhooks(request: Request) -> WebhookList:
    """The organization's outbound webhooks."""
    org_id, _, _ = _require_admin(request)
    hooks = await outbound_webhooks.list_webhooks(
        getattr(request.app.state, "db_manager", None), org_id
    )
    return WebhookList(webhooks=[_webhook_view(h) for h in hooks])


@router.post("/admin/webhooks", response_model=CreatedWebhookView, status_code=201)
async def create_webhook(body: WebhookBody, request: Request) -> CreatedWebhookView:
    """Add an outbound webhook; its signing secret is returned once."""
    org_id, user_id, _ = _require_admin(request)
    try:
        created = await outbound_webhooks.create_webhook(
            getattr(request.app.state, "db_manager", None),
            org_id,
            body.url,
            body.events,
            body.description,
        )
    except OutboundWebhookError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    await _audit(
        request,
        org_id,
        user_id,
        "webhook.created",
        {"webhook_id": created.webhook.id, "url": created.webhook.url},
    )
    return CreatedWebhookView(**_webhook_view(created.webhook).model_dump(), secret=created.secret)


@router.delete("/admin/webhooks/{webhook_id}", status_code=204)
async def remove_webhook(webhook_id: str, request: Request) -> None:
    """Delete an outbound webhook (its queued reports go with it)."""
    org_id, user_id, _ = _require_admin(request)
    if not await outbound_webhooks.delete_webhook(
        getattr(request.app.state, "db_manager", None), org_id, webhook_id
    ):
        raise HTTPException(status_code=404, detail="Webhook not found")
    await _audit(request, org_id, user_id, "webhook.deleted", {"webhook_id": webhook_id})
