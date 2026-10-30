"""Webhooks that report an organization's outcomes: a turn completed or failed, a pull request
opened, a spend limit blocked a turn.

Each report is stored (``outbound_deliveries``) before it is sent, so a restart loses none, then
sent at once in the background; a failure is retried with exponential backoff by the periodic
sweeper until ``MAX_ATTEMPTS``. Requests go through the SSRF-safe client and carry an HMAC of
``<timestamp>.<body>`` with the webhook's secret (``X-Rocket-Signature: sha256=<hex>``), which
is sealed at rest and shown once, when the webhook is created.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

from agent_core.tools.safe_http import UnsafeURLError, resolve_public_target, safe_post
from config_engine.crypto import CredentialCipher
from sqlalchemy import delete, select, update

from api.db.models import OutboundDeliveryModel, OutboundWebhookModel
from specifications.interfaces.automation import OUTBOUND_EVENTS, OutboundWebhook

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 6
# Seconds before attempt n+1: 30s, 2m, 8m, 32m, ~2h.
BACKOFF_BASE_SECONDS = 30
BACKOFF_FACTOR = 4
SEND_TIMEOUT_SECONDS = 10.0
# A claimed attempt is not taken by another sender for this long (longer than a send).
CLAIM_SECONDS = 120
# A report is a summary, never a transcript: every text field is cut to this.
MAX_FIELD_CHARS = 2000
MAX_WEBHOOKS_PER_ORG = 20

_background: set[asyncio.Task[None]] = set()
# Without a database (development and tests): webhooks by organization, with their secret.
_memory: dict[str, dict[str, tuple[OutboundWebhook, str]]] = {}
_memory_deliveries: list[dict[str, Any]] = []


class OutboundWebhookError(ValueError):
    """A webhook that cannot be saved as given."""


@dataclass(frozen=True)
class CreatedWebhook:
    webhook: OutboundWebhook
    # Shown once: the receiver needs it to check signatures.
    secret: str


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """``sha256=<hex>`` of HMAC-SHA256(secret, "<timestamp>.<body>"): the timestamp is signed
    so a captured request cannot be replayed later with a new one."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _clamped(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value[:MAX_FIELD_CHARS] if isinstance(value, str) else value
        for key, value in payload.items()
    }


def _from_row(row: OutboundWebhookModel) -> OutboundWebhook:
    return OutboundWebhook(
        id=row.id,
        org_id=row.org_id,
        url=row.url,
        events=tuple(row.events or ()),
        secret_fingerprint=row.secret_fingerprint,
        enabled=row.enabled,
        description=row.description,
    )


async def _validated_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("https://"):
        raise OutboundWebhookError("A webhook URL starts with https://.")
    try:
        await resolve_public_target(url)
    except UnsafeURLError as err:
        raise OutboundWebhookError(str(err)) from err
    return url


async def create_webhook(
    db_mgr: Any, org_id: str, url: str, events: list[str], description: str = ""
) -> CreatedWebhook:
    unknown = sorted(set(events) - set(OUTBOUND_EVENTS))
    if unknown or not events:
        raise OutboundWebhookError(
            f"Events are among {', '.join(OUTBOUND_EVENTS)}"
            + (f"; unknown: {', '.join(unknown)}" if unknown else ".")
        )
    if len(await list_webhooks(db_mgr, org_id)) >= MAX_WEBHOOKS_PER_ORG:
        raise OutboundWebhookError(f"An organization has at most {MAX_WEBHOOKS_PER_ORG} webhooks.")
    secret = f"whsec_{secrets.token_urlsafe(32)}"
    webhook = OutboundWebhook(
        id=f"owh_{uuid.uuid4().hex[:12]}",
        org_id=org_id,
        url=await _validated_url(url),
        events=tuple(dict.fromkeys(events)),
        secret_fingerprint=CredentialCipher.fingerprint(secret),
        description=description.strip()[:255],
    )
    if db_mgr is None:
        _memory.setdefault(org_id, {})[webhook.id] = (webhook, secret)
        return CreatedWebhook(webhook, secret)
    async with db_mgr.session(tenant_org_id=org_id) as session:
        session.add(
            OutboundWebhookModel(
                id=webhook.id,
                org_id=org_id,
                url=webhook.url,
                events=list(webhook.events),
                secret_encrypted=CredentialCipher().encrypt(secret),
                secret_fingerprint=webhook.secret_fingerprint,
                description=webhook.description,
            )
        )
    return CreatedWebhook(webhook, secret)


async def list_webhooks(db_mgr: Any, org_id: str) -> list[OutboundWebhook]:
    if db_mgr is None:
        return [hook for hook, _ in _memory.get(org_id, {}).values()]
    async with db_mgr.session(tenant_org_id=org_id) as session:
        rows = (
            await session.execute(
                select(OutboundWebhookModel).where(OutboundWebhookModel.org_id == org_id)
            )
        ).scalars()
        return [_from_row(row) for row in rows]


async def delete_webhook(db_mgr: Any, org_id: str, webhook_id: str) -> bool:
    if db_mgr is None:
        return _memory.get(org_id, {}).pop(webhook_id, None) is not None
    async with db_mgr.session(tenant_org_id=org_id) as session:
        result = await session.execute(
            delete(OutboundWebhookModel).where(
                OutboundWebhookModel.id == webhook_id, OutboundWebhookModel.org_id == org_id
            )
        )
        return bool(getattr(result, "rowcount", 0))


# -- emitting and sending -----------------------------------------------------------------------


async def emit(db_mgr: Any, org_id: str, event: str, payload: dict[str, Any]) -> int:
    """Queue ``event`` for each of the organization's webhooks that subscribe to it and send it
    in the background; how many were queued. Never raises: a report must not fail a turn."""
    try:
        hooks = [h for h in await list_webhooks(db_mgr, org_id) if h.enabled and event in h.events]
        if not hooks:
            return 0
        body = {"event": event, "org_id": org_id, "at": int(time.time()), "data": _clamped(payload)}
        ids = [await _queue(db_mgr, org_id, hook.id, event, body) for hook in hooks]
    except Exception as err:
        logger.warning("Could not queue the %s report of %s: %s", event, org_id, err)
        return 0
    task = asyncio.create_task(_send_queued(db_mgr, org_id, ids))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return len(ids)


async def _queue(
    db_mgr: Any, org_id: str, webhook_id: str, event: str, body: dict[str, Any]
) -> str:
    delivery_id = f"od_{uuid.uuid4().hex[:16]}"
    now = int(time.time())
    if db_mgr is None:
        _memory_deliveries.append(
            {"id": delivery_id, "org_id": org_id, "webhook_id": webhook_id, "event": event,
             "payload": body, "status": "pending", "attempts": 0, "next_attempt_at": now}
        )  # fmt: skip
        return delivery_id
    async with db_mgr.session(tenant_org_id=org_id) as session:
        session.add(
            OutboundDeliveryModel(
                id=delivery_id,
                org_id=org_id,
                webhook_id=webhook_id,
                event=event,
                payload=body,
                next_attempt_at=now,
            )
        )
    return delivery_id


async def _send_queued(db_mgr: Any, org_id: str, delivery_ids: list[str]) -> None:
    for delivery_id in delivery_ids:
        try:
            await attempt_delivery(db_mgr, org_id, delivery_id)
        except Exception as err:
            logger.warning("Outbound webhook delivery %s failed: %s", delivery_id, err)


async def attempt_delivery(db_mgr: Any, org_id: str, delivery_id: str) -> str:
    """Send one queued report; its status afterwards (delivered, pending or failed)."""
    loaded = await _load(db_mgr, org_id, delivery_id)
    if loaded is None:
        return "gone"
    delivery, url, secret = loaded
    body = json.dumps(delivery["payload"], separators=(",", ":"), sort_keys=True).encode()
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "RocketChat-Webhooks/1",
        "X-Rocket-Event": str(delivery["event"]),
        "X-Rocket-Delivery": delivery_id,
        "X-Rocket-Timestamp": str(timestamp),
        "X-Rocket-Signature": sign(secret, timestamp, body),
    }
    error = ""
    try:
        status, _ = await safe_post(
            url, content=body, headers=headers, timeout_seconds=SEND_TIMEOUT_SECONDS
        )
        if not 200 <= status < 300:
            error = f"HTTP {status}"
    except Exception as err:
        error = f"{type(err).__name__}: {err}"[:500]
    return await _record_attempt(db_mgr, org_id, delivery_id, int(delivery["attempts"]) + 1, error)


def next_attempt_delay(attempts: int) -> int:
    return int(BACKOFF_BASE_SECONDS * BACKOFF_FACTOR ** max(0, attempts - 1))


async def _load(
    db_mgr: Any, org_id: str, delivery_id: str
) -> tuple[dict[str, Any], str, str] | None:
    if db_mgr is None:
        found = next((d for d in _memory_deliveries if d["id"] == delivery_id), None)
        hook = _memory.get(org_id, {}).get(str(found["webhook_id"])) if found else None
        if found is None or hook is None or found["status"] != "pending":
            return None
        return found, hook[0].url, hook[1]
    now = int(time.time())
    async with db_mgr.session(tenant_org_id=org_id) as session:
        # Claim the attempt: moving its due time forward is atomic, so of the sender that queued
        # it and the sweepers of every replica, one sends it.
        claimed = await session.execute(
            update(OutboundDeliveryModel)
            .where(
                OutboundDeliveryModel.id == delivery_id,
                OutboundDeliveryModel.status == "pending",
                OutboundDeliveryModel.next_attempt_at <= now,
            )
            .values(next_attempt_at=now + CLAIM_SECONDS)
        )
        if not getattr(claimed, "rowcount", 0):
            return None
        row = await session.get(OutboundDeliveryModel, delivery_id)
        hook = await session.get(OutboundWebhookModel, row.webhook_id) if row else None
        if row is None or hook is None or not hook.enabled:
            return None
        delivery = {"event": row.event, "payload": row.payload, "attempts": row.attempts}
        return delivery, hook.url, CredentialCipher().decrypt(hook.secret_encrypted)


async def _record_attempt(
    db_mgr: Any, org_id: str, delivery_id: str, attempts: int, error: str
) -> str:
    status = "delivered" if not error else ("failed" if attempts >= MAX_ATTEMPTS else "pending")
    next_at = int(time.time()) + next_attempt_delay(attempts)
    if db_mgr is None:
        for delivery in _memory_deliveries:
            if delivery["id"] == delivery_id:
                delivery.update(
                    status=status, attempts=attempts, next_attempt_at=next_at, last_error=error
                )
        return status
    async with db_mgr.session(tenant_org_id=org_id) as session:
        row = await session.get(OutboundDeliveryModel, delivery_id)
        if row is not None:
            row.status, row.attempts, row.next_attempt_at, row.last_error = (
                status, attempts, next_at, error,
            )  # fmt: skip
    if status == "failed":
        logger.warning(
            "Outbound webhook delivery %s gave up after %d attempts: %s",
            delivery_id,
            attempts,
            error,
        )
    return status


async def retry_due(admin_db: Any, db_mgr: Any, limit: int = 100) -> int:
    """Send again the reports whose next attempt is due (the periodic sweeper). The due rows of
    every organization are listed on the schema-owner connection; each is sent in its tenant."""
    if admin_db is None:
        due = [
            (d["org_id"], d["id"])
            for d in _memory_deliveries
            if d["status"] == "pending"
            and d["attempts"] > 0
            and d["next_attempt_at"] <= time.time()
        ][:limit]
    else:
        async with admin_db.session() as session:
            due = [
                (row.org_id, row.id)
                for row in (
                    await session.execute(
                        select(OutboundDeliveryModel)
                        .where(
                            OutboundDeliveryModel.status == "pending",
                            OutboundDeliveryModel.next_attempt_at <= int(time.time()),
                        )
                        .limit(limit)
                    )
                ).scalars()
            ]
    for org_id, delivery_id in due:
        await attempt_delivery(db_mgr, org_id, delivery_id)
    return len(due)


def reset_memory() -> None:
    _memory.clear()
    _memory_deliveries.clear()


# Delivered and abandoned reports are kept this long for administrators to look at.
FINISHED_RETENTION_SECONDS = 7 * 24 * 3600


async def forget_finished(admin_db: Any) -> int:
    """Retention of delivered and failed reports, every organization's (schema owner)."""
    if admin_db is None:
        return 0
    cutoff = int(time.time()) - FINISHED_RETENTION_SECONDS
    async with admin_db.session() as session:
        result = await session.execute(
            delete(OutboundDeliveryModel).where(
                OutboundDeliveryModel.status != "pending",
                OutboundDeliveryModel.created_at < cutoff,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)
