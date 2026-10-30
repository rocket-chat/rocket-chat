"""Triggers, webhook deliveries and outbound webhooks are each organization's own, under forced
Row-Level Security."""

from __future__ import annotations

import time

from api.db.models import (
    OutboundDeliveryModel,
    OutboundWebhookModel,
    TriggerModel,
    WebhookDeliveryModel,
)
from api.db.session import DatabaseSessionManager

from tests.support.rls import (
    TEST_DATABASE_URL,
    add_row,
    assert_cannot_write_into,
    assert_isolated,
    needs_postgres,
    new_id,
    two_orgs,
)


def _trigger(org: str) -> TriggerModel:
    now = int(time.time())
    return TriggerModel(
        id=new_id("trg"), org_id=org, name="t", source="github_pr", filter={},
        created_at=now, updated_at=now,
    )  # fmt: skip


def _delivery(org: str) -> WebhookDeliveryModel:
    return WebhookDeliveryModel(
        id=new_id("dlv"), org_id=org, source="github", delivery_id=new_id("d"),
        created_at=int(time.time()),
    )  # fmt: skip


def _webhook(org: str, webhook_id: str | None = None) -> OutboundWebhookModel:
    return OutboundWebhookModel(
        id=webhook_id or new_id("owh"), org_id=org, url="https://example.com/hook",
        events=["turn.completed"], secret_encrypted="x", created_at=int(time.time()),
    )  # fmt: skip


def _outbound(org: str, webhook_id: str) -> OutboundDeliveryModel:
    now = int(time.time())
    return OutboundDeliveryModel(
        id=new_id("od"), org_id=org, webhook_id=webhook_id, event="turn.completed",
        payload={}, next_attempt_at=now, created_at=now,
    )  # fmt: skip


@needs_postgres
async def test_automation_tables_are_isolated_in_postgres() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org_a, org_b = await two_orgs(db)
        webhook_id = new_id("owh")
        await add_row(db, org_a, _webhook(org_a, webhook_id))
        await assert_isolated(db, OutboundWebhookModel, org_a, org_b)
        await assert_cannot_write_into(db, org_b, _webhook(org_a))
        for make, model in (
            (_trigger, TriggerModel),
            (_delivery, WebhookDeliveryModel),
            (lambda org: _outbound(org, webhook_id), OutboundDeliveryModel),
        ):
            await add_row(db, org_a, make(org_a))
            await assert_isolated(db, model, org_a, org_b)
            await assert_cannot_write_into(db, org_b, make(org_a))
    finally:
        await db.close()
