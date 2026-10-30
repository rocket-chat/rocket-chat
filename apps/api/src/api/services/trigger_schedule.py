"""Schedule triggers: each minute, the ``schedule`` triggers whose cron expression (UTC) matches
start a run. Every replica checks; the run's delivery id (``<trigger>:<minute>``) is claimed in
``webhook_deliveries``, so one replica starts it."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from croniter import croniter
from fastapi import FastAPI
from sqlalchemy import select

from api.db.models import TriggerModel
from api.services import triggers as trigger_store
from api.services.triggers import _from_row, claim_delivery, start_from_trigger
from specifications.interfaces.automation import Trigger, TriggerEvent

logger = logging.getLogger(__name__)


async def _schedule_triggers(admin_db: Any) -> list[tuple[Trigger, str]]:
    """Every organization's enabled schedule triggers, with who created each (it owns its runs).
    A cross-tenant read, on the schema-owner connection like the mission claimer."""
    if admin_db is None:
        return [
            (t, "scheduler")
            for org in trigger_store._memory.values()
            for t in org.values()
            if t.source == "schedule" and t.enabled
        ]
    async with admin_db.session() as session:
        rows = (
            await session.execute(
                select(TriggerModel).where(
                    TriggerModel.source == "schedule", TriggerModel.enabled.is_(True)
                )
            )
        ).scalars()
        return [(_from_row(row), row.created_by or "scheduler") for row in rows]


async def fire_due_schedules(app: FastAPI, admin_db: Any, now: float | None = None) -> int:
    """Start the runs due this minute; how many this replica started."""
    minute = datetime.fromtimestamp(int((now or time.time()) // 60 * 60), tz=UTC)
    stamp = minute.strftime("%Y%m%d%H%M")
    db_mgr = getattr(app.state, "db_manager", None)
    started = 0
    for trigger, owner in await _schedule_triggers(admin_db):
        try:
            if not croniter.match(trigger.schedule, minute):
                continue
        except (ValueError, KeyError):
            logger.warning("Schedule trigger %s has an invalid cron expression", trigger.id)
            continue
        delivery = f"{trigger.id}:{stamp}"
        if not await claim_delivery(db_mgr, trigger.org_id, "schedule", delivery):
            continue
        event = TriggerEvent(
            source="schedule",
            delivery_id=delivery,
            key=f"{trigger.id}-{stamp}",
            title=trigger.name,
            body=f"Scheduled run of '{trigger.name}' ({minute:%Y-%m-%d %H:%M} UTC).",
            actor=owner,
            trusted=True,
        )
        try:
            await start_from_trigger(app, trigger, event, owner)
            started += 1
        except Exception as err:
            logger.exception("Schedule trigger %s could not start: %s", trigger.id, err)
    return started
