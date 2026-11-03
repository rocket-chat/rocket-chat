"""From a parsed event to mission runs: which organization it belongs to, which missions take it,
and whether this delivery was already handled.

Forge webhooks carry no Rocket identity: the organization is the one that mapped the installation
the event came through (only an unmapped installation falls back to ``ROCKET_WEBHOOK_ORG_ID``).
Every enabled mission with a trigger that takes the event starts a run, each claimed once per
delivery in ``webhook_deliveries`` (unique per organization, source and delivery), so several
replicas receiving the same delivery start each run once. An organization with no mission on
forge events keeps the deployment's built-in ones (``GITHUB_TRIGGER_LABEL``), run as the event's
author as before.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

from fastapi import FastAPI
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from api.db.models import WebhookDeliveryModel
from api.services.github_installations import installation_org
from api.services.mission_delivery import mission_store
from api.services.mission_runs import RunOutcome, start_run
from specifications.interfaces.automation import (
    OWN_SOURCES,
    Delivery,
    Mission,
    MissionTrigger,
    TriggerEvent,
    TriggerFilter,
    matches,
)

_FORGE_PREFIX = {"github": "gh", "gitlab": "gl", "jira": "jira", "linear": "linear"}
# Deliveries are remembered this long: the forges retry a delivery for minutes, not days.
DELIVERY_RETENTION_SECONDS = 24 * 3600
# Without a database (development and tests): the claimed deliveries.
_claimed: dict[tuple[str, str, str], float] = {}


async def webhook_identity(
    app: FastAPI, sender_login: str, installation_id: int | None, forge: str = "github"
) -> tuple[str, str]:
    """The organization an event belongs to, and the member made for its author."""
    db_mgr = getattr(app.state, "db_manager", None)
    mapped = (
        await installation_org(db_mgr, installation_id) if installation_id is not None else None
    )
    org_id: str = mapped or os.getenv("ROCKET_WEBHOOK_ORG_ID") or "default_org"
    safe_login = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in sender_login)
    return org_id, f"{_FORGE_PREFIX.get(forge, forge)}_{safe_login}"


def default_missions(org_id: str) -> list[Mission]:
    """What an organization with no mission on forge events gets: the deployment's label
    (``GITHUB_TRIGGER_LABEL``) on an issue, and comments that address the agent or sit on such
    an issue. Not stored: they run as the event's author, like the triggers they replace."""
    label = os.getenv("GITHUB_TRIGGER_LABEL", "ai-fix")
    return [
        Mission(
            id="default_issues",
            org_id=org_id,
            name=f"Issues labelled {label}",
            instructions="",
            scope="org",
            triggers=(
                MissionTrigger("github_issue_label", TriggerFilter(labels=(label,))),
                MissionTrigger("github_issue_comment"),
                MissionTrigger("github_issue_comment", TriggerFilter(labels=(label,))),
            ),
            deliveries=(Delivery("session"),),
        )
    ]


async def missions_for(app: FastAPI, org_id: str, event: TriggerEvent) -> list[Mission]:
    """The organization's enabled missions that take ``event`` (the built-in ones when it has
    none on forge events)."""
    store = mission_store(app)
    configured = await store.all_for(org_id)
    on_events = [m for m in configured if any(t.source not in OWN_SOURCES for t in m.triggers)]
    candidates = on_events or default_missions(org_id)
    return [m for m in candidates if m.enabled and any(matches(t, event) for t in m.triggers)]


async def receive_trigger_event(
    app: FastAPI, event: TriggerEvent, org_id: str | None = None
) -> RunOutcome:
    """Start (or wake) the run of every mission that takes the event, once per delivery; the
    first run's outcome is reported to the forge."""
    forge = event.source.split("_", 1)[0]
    mapped_org, author = await webhook_identity(app, event.actor, event.installation_id, forge)
    org_id = org_id or mapped_org
    db_mgr = getattr(app.state, "db_manager", None)
    missions = await missions_for(app, org_id, event)
    if not missions:
        return RunOutcome("ignored", reason="No mission of the organization takes this event")
    outcomes: list[RunOutcome] = []
    for mission in missions:
        claim = f"{mission.id}:{event.delivery_id}" if event.delivery_id else ""
        if not await claim_delivery(db_mgr, org_id, forge, claim):
            outcomes.append(RunOutcome("duplicate", mission_id=mission.id))
            continue
        outcomes.append(await start_run(app, mission, event, fallback_owner=author))
    return next((o for o in outcomes if o.status in ("created", "resumed")), outcomes[0])


async def claim_delivery(db_mgr: Any, org_id: str, source: str, delivery_id: str) -> bool:
    """Whether this replica is the first to handle the delivery (a delivery without an id is
    always handled: the source gave nothing to recognise it by)."""
    if not delivery_id:
        return True
    if db_mgr is None or getattr(db_mgr, "session_factory", None) is None:
        key = (org_id, source, delivery_id)
        now = time.time()
        for stale in [k for k, at in _claimed.items() if now - at > DELIVERY_RETENTION_SECONDS]:
            _claimed.pop(stale, None)
        if key in _claimed:
            return False
        _claimed[key] = now
        return True
    try:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            session.add(
                WebhookDeliveryModel(
                    id=f"dlv_{uuid.uuid4().hex[:16]}",
                    org_id=org_id,
                    source=source,
                    delivery_id=delivery_id[:128],
                )
            )
    except IntegrityError:
        return False
    return True


async def forget_old_deliveries(admin_db: Any) -> int:
    """Retention: deliveries older than a day are not redelivered any more. Every
    organization's, on the schema-owner connection."""
    if admin_db is None:
        return 0
    cutoff = int(time.time()) - DELIVERY_RETENTION_SECONDS
    async with admin_db.session() as session:
        result = await session.execute(
            delete(WebhookDeliveryModel).where(WebhookDeliveryModel.created_at < cutoff)
        )
        return int(getattr(result, "rowcount", 0) or 0)


def reset_memory() -> None:
    _claimed.clear()
