"""Delayed mission runs: an event a trigger takes with ``delay_minutes`` starts its run later.

``start_or_delay`` is what every event source calls instead of ``start_run``: without a delay the
run starts at once; with one, the event is stored (``mission_delayed_runs``) and the mission
scheduler starts the run once it is due (``claim_due``). The event is kept as it was received,
so the run reads exactly what it would have read at once, and the mission is read again when the
run starts: one switched off or deleted meanwhile starts nothing.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, fields
from typing import Any, ClassVar

from fastapi import FastAPI
from sqlalchemy import delete, func, select

from api.db.models import MissionDelayedRunModel
from api.db.session import current_tenant_org_id
from api.services.mission_delivery import mission_store
from api.services.mission_runs import RunOutcome, start_run
from api.services.missions import MissionError
from specifications.interfaces.automation import (
    MAX_DELAYED_RUNS_PER_MISSION,
    Mission,
    TriggerEvent,
    trigger_delay,
)

logger = logging.getLogger(__name__)

_TUPLES = frozenset({"labels"})


def event_json(event: TriggerEvent) -> dict[str, Any]:
    return {key: list(value) if key in _TUPLES else value for key, value in asdict(event).items()}


def event_from_json(raw: dict[str, Any]) -> TriggerEvent:
    known = {f.name for f in fields(TriggerEvent)}
    values = {key: value for key, value in raw.items() if key in known}
    for key in _TUPLES & values.keys():
        values[key] = tuple(str(v) for v in values[key] or ())
    return TriggerEvent(**values)


@dataclass(frozen=True)
class DelayedRun:
    id: str
    org_id: str
    mission_id: str
    event: TriggerEvent
    fallback_owner: str
    due_at: int
    created_at: int


def _from_row(row: MissionDelayedRunModel) -> DelayedRun:
    return DelayedRun(
        id=row.id,
        org_id=row.org_id,
        mission_id=row.mission_id,
        event=event_from_json(dict(row.event or {})),
        fallback_owner=row.fallback_owner,
        due_at=row.due_at,
        created_at=row.created_at,
    )


class DelayedRunStore:
    """Delayed runs in PostgreSQL (tenant-scoped); a process-local dict without a database."""

    _MEMORY: ClassVar[dict[str, DelayedRun]] = {}

    def __init__(self, db_mgr: Any) -> None:
        self._db = (
            db_mgr if db_mgr is not None and getattr(db_mgr, "session_factory", None) else None
        )

    async def add(
        self, mission: Mission, event: TriggerEvent, fallback_owner: str, due_at: int
    ) -> DelayedRun:
        """Keep the event until ``due_at``; refused once the mission has too many waiting."""
        run = DelayedRun(
            id=f"mdr_{uuid.uuid4().hex[:16]}",
            org_id=mission.org_id,
            mission_id=mission.id,
            event=event,
            fallback_owner=fallback_owner,
            due_at=due_at,
            created_at=int(time.time()),
        )
        if self._db is None:
            if len(await self.of_mission(mission.org_id, mission.id)) >= (
                MAX_DELAYED_RUNS_PER_MISSION
            ):
                raise MissionError("This mission has too many runs waiting to start.")
            self._MEMORY[run.id] = run
            return run
        async with self._db.session(tenant_org_id=mission.org_id) as session:
            waiting = await session.scalar(
                select(func.count())
                .select_from(MissionDelayedRunModel)
                .where(MissionDelayedRunModel.mission_id == mission.id)
            )
            if (waiting or 0) >= MAX_DELAYED_RUNS_PER_MISSION:
                raise MissionError("This mission has too many runs waiting to start.")
            session.add(
                MissionDelayedRunModel(
                    id=run.id,
                    org_id=run.org_id,
                    mission_id=run.mission_id,
                    event=event_json(event),
                    fallback_owner=fallback_owner,
                    due_at=due_at,
                    created_at=run.created_at,
                )
            )
        return run

    async def of_mission(self, org_id: str, mission_id: str) -> list[DelayedRun]:
        """The mission's runs still waiting, the next one first."""
        if self._db is None:
            waiting = [
                r
                for r in self._MEMORY.values()
                if r.org_id == org_id and r.mission_id == mission_id
            ]
            return sorted(waiting, key=lambda r: r.due_at)
        async with self._db.session(tenant_org_id=org_id) as session:
            rows = await session.execute(
                select(MissionDelayedRunModel)
                .where(
                    MissionDelayedRunModel.org_id == org_id,
                    MissionDelayedRunModel.mission_id == mission_id,
                )
                .order_by(MissionDelayedRunModel.due_at)
            )
            return [_from_row(row) for row in rows.scalars()]

    async def claim_due(self, admin_db: Any, now: float | None = None) -> list[DelayedRun]:
        """Due runs, each claimed by deleting its row. Runs on the schema-owner connection (it
        must see every tenant); a replica that loses the race gets ``rowcount == 0``."""
        moment = int(now if now is not None else time.time())
        if admin_db is None or self._db is None:
            due = [r for r in self._MEMORY.values() if r.due_at <= moment]
            for run in due:
                self._MEMORY.pop(run.id, None)
            return sorted(due, key=lambda r: r.due_at)
        claimed: list[DelayedRun] = []
        async with admin_db.session() as session:
            rows = await session.execute(
                select(MissionDelayedRunModel)
                .where(MissionDelayedRunModel.due_at <= moment)
                .order_by(MissionDelayedRunModel.due_at)
            )
            for row in rows.scalars().all():
                run = _from_row(row)
                result = await session.execute(
                    delete(MissionDelayedRunModel).where(MissionDelayedRunModel.id == run.id)
                )
                if getattr(result, "rowcount", 0):
                    claimed.append(run)
        return claimed


def delayed_run_store(app: FastAPI) -> DelayedRunStore:
    return DelayedRunStore(getattr(app.state, "db_manager", None))


async def start_or_delay(
    app: FastAPI, mission: Mission, event: TriggerEvent, fallback_owner: str = ""
) -> RunOutcome:
    """Start the run now, or keep the event until the delay of the trigger that took it."""
    delay = trigger_delay(mission.triggers, event)
    if not delay:
        return await start_run(app, mission, event, fallback_owner=fallback_owner)
    due_at = int(time.time()) + delay
    token = current_tenant_org_id.set(mission.org_id)
    try:
        await delayed_run_store(app).add(mission, event, fallback_owner, due_at)
    except MissionError as err:
        return RunOutcome("ignored", mission_id=mission.id, reason=str(err))
    finally:
        current_tenant_org_id.reset(token)
    minutes = delay // 60
    return RunOutcome(
        "scheduled",
        mission_id=mission.id,
        reason=f"The run starts in {minutes} minute{'s' if minutes != 1 else ''}.",
        starts_at=due_at,
    )


async def start_due_runs(app: FastAPI, admin_db: Any) -> None:
    """Start every delayed run that is due, if its mission still exists and is switched on."""
    store = mission_store(app)
    for run in await delayed_run_store(app).claim_due(admin_db):
        mission = await store.get(run.org_id, run.mission_id)
        if mission is None or not mission.enabled:
            logger.info("Delayed run %s dropped: its mission is gone or off", run.id)
            continue
        try:
            await start_run(app, mission, run.event, fallback_owner=run.fallback_owner)
        except Exception as err:
            logger.exception("Delayed run of mission %s could not start: %s", mission.id, err)
            await store.mark_run(mission.org_id, mission.id, None, "failed")
