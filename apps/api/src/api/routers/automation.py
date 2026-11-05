"""The organization's outbound webhooks (administrators): signed reports of every turn and pull
request outcome, whatever started it. What starts work on its own is a mission (``missions.py``)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _require_admin
from api.services import outbound_webhooks
from api.services.audit import AuditService
from api.services.outbound_webhooks import OutboundWebhookError
from specifications.interfaces.automation import OutboundWebhook

router = APIRouter(prefix="/v1", tags=["Automation"])


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


async def _audit(
    request: Request, org_id: str, actor: str, event: str, details: dict[str, Any]
) -> None:
    audit = getattr(request.app.state, "audit_service", None)
    if isinstance(audit, AuditService):
        await audit.record(event, org_id=org_id, actor_user_id=actor, metadata=details)


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
