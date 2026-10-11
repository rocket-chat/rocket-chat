"""Scheduled background missions.

A mission is a prompt the platform runs on a cron schedule as an ordinary session owned by its
creator, so it carries the creator's permissions, budget, permission mode and audit trail.
Scheduling is deliberately conservative: a minimum interval, a cap per member and per
organization, and an atomic claim so a firing starts exactly one session however many API
replicas are running.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import select, update

from api.db.models import ScheduledMissionModel
from api.db.session import DatabaseSessionManager

MIN_INTERVAL_SECONDS = 300
MAX_MISSIONS_PER_USER = 20
MAX_MISSIONS_PER_ORG = 200


class MissionError(ValueError):
    """The mission is not acceptable as submitted."""


@dataclass
class Mission:
    id: str
    org_id: str
    user_id: str
    name: str
    prompt: str
    cron: str
    timezone: str = "UTC"
    agent_id: str | None = None
    git_repo: str | None = None
    git_branch: str | None = None
    permission_mode: str | None = None
    # Slack channel id the result is posted to when a run finishes.
    slack_channel: str | None = None
    enabled: bool = True
    next_run_at: int | None = None
    last_run_at: int | None = None
    last_session_id: str | None = None
    last_status: str | None = None
    created_at: int = 0


def next_fire_after(cron: str, timezone: str, after: float) -> int:
    """The next firing strictly after ``after`` (epoch seconds), validating the schedule.

    A schedule that fires more often than every five minutes is refused: an unattended agent
    on a one-minute loop is a cost incident, not a feature.
    """
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as err:
        raise MissionError(f"Unknown timezone '{timezone}'.") from err
    if not croniter.is_valid(cron) or len(cron.split()) != 5:
        raise MissionError("The schedule must be a five-field cron expression, e.g. '0 9 * * 1-5'.")
    start = datetime.fromtimestamp(after, tz=zone)
    iterator = croniter(cron, start)
    first = iterator.get_next(datetime)
    second = iterator.get_next(datetime)
    if (second - first).total_seconds() < MIN_INTERVAL_SECONDS:
        raise MissionError("A mission may not run more often than every 5 minutes.")
    return int(first.astimezone(UTC).timestamp())


def _to_mission(row: ScheduledMissionModel) -> Mission:
    return Mission(
        **{c.name: getattr(row, c.name) for c in ScheduledMissionModel.__table__.columns}
    )


class MissionStore:
    """Missions in PostgreSQL (tenant-scoped); a process-local dict without a database."""

    def __init__(self, db_manager: DatabaseSessionManager | None) -> None:
        self._db = (
            db_manager
            if db_manager is not None and getattr(db_manager, "session_factory", None)
            else None
        )
        self._memory: dict[str, Mission] = {}

    async def create(self, mission: Mission) -> Mission:
        mission.id = f"mission_{uuid.uuid4().hex[:10]}"
        mission.created_at = int(time.time())
        mission.next_run_at = next_fire_after(mission.cron, mission.timezone, time.time())
        await self._enforce_caps(mission.org_id, mission.user_id)
        if self._db is None:
            self._memory[mission.id] = mission
            return mission
        async with self._db.session(tenant_org_id=mission.org_id) as session:
            session.add(ScheduledMissionModel(**vars(mission)))
        return mission

    async def _enforce_caps(self, org_id: str, user_id: str) -> None:
        missions = await self.list(org_id, None)
        if len(missions) >= MAX_MISSIONS_PER_ORG:
            raise MissionError("This organization has reached its scheduled mission limit.")
        if sum(1 for m in missions if m.user_id == user_id) >= MAX_MISSIONS_PER_USER:
            raise MissionError("You have reached your scheduled mission limit.")

    async def list(self, org_id: str, user_id: str | None) -> list[Mission]:
        if self._db is None:
            found = [m for m in self._memory.values() if m.org_id == org_id]
        else:
            async with self._db.session(tenant_org_id=org_id) as session:
                stmt = select(ScheduledMissionModel).where(ScheduledMissionModel.org_id == org_id)
                found = [_to_mission(r) for r in (await session.execute(stmt)).scalars()]
        owned = [m for m in found if user_id is None or m.user_id == user_id]
        return sorted(owned, key=lambda m: m.created_at)

    async def get(self, org_id: str, mission_id: str) -> Mission | None:
        return next((m for m in await self.list(org_id, None) if m.id == mission_id), None)

    async def update(self, org_id: str, mission_id: str, **changes: Any) -> Mission | None:
        mission = await self.get(org_id, mission_id)
        if mission is None:
            return None
        for key, value in changes.items():
            setattr(mission, key, value)
        if {"cron", "timezone", "enabled"} & changes.keys() and mission.enabled:
            mission.next_run_at = next_fire_after(mission.cron, mission.timezone, time.time())
        elif not mission.enabled:
            mission.next_run_at = None
        if self._db is None:
            self._memory[mission_id] = mission
            return mission
        async with self._db.session(tenant_org_id=org_id) as session:
            await session.execute(
                update(ScheduledMissionModel)
                .where(ScheduledMissionModel.id == mission_id)
                .values(**{k: v for k, v in vars(mission).items() if k not in ("id", "org_id")})
            )
        return mission

    async def mark_run(
        self, org_id: str, mission_id: str, session_id: str | None, status: str
    ) -> None:
        """Remember the latest run (and its session) so the page can link to it."""
        fields = {
            "last_run_at": int(time.time()),
            "last_session_id": session_id,
            "last_status": status,
        }
        if self._db is None:
            mission = self._memory.get(mission_id)
            if mission is not None:
                for key, value in fields.items():
                    setattr(mission, key, value)
            return
        async with self._db.session(tenant_org_id=org_id) as session:
            await session.execute(
                update(ScheduledMissionModel)
                .where(ScheduledMissionModel.id == mission_id)
                .values(**fields)
            )

    async def delete(self, org_id: str, mission_id: str) -> bool:
        if self._db is None:
            return self._memory.pop(mission_id, None) is not None
        async with self._db.session(tenant_org_id=org_id) as session:
            row = await session.get(ScheduledMissionModel, mission_id)
            if row is None:
                return False
            await session.delete(row)
        return True


async def claim_due(
    admin_db: DatabaseSessionManager | None, store: MissionStore, now: float | None = None
) -> list[Mission]:
    """Missions whose time has come, each claimed by moving ``next_run_at`` forward atomically.

    Runs on the schema-owner connection (it must see every tenant). A replica that loses the
    race for a row gets ``rowcount == 0`` and skips it, so a firing never starts two sessions.
    """
    moment = now if now is not None else time.time()
    claimed: list[Mission] = []
    if admin_db is None:
        for mission in list(store._memory.values()):
            if (
                mission.enabled
                and mission.next_run_at is not None
                and mission.next_run_at <= moment
            ):
                mission.next_run_at = next_fire_after(mission.cron, mission.timezone, moment)
                claimed.append(mission)
        return claimed
    async with admin_db.session() as session:
        due = (
            (
                await session.execute(
                    select(ScheduledMissionModel).where(
                        ScheduledMissionModel.enabled.is_(True),
                        ScheduledMissionModel.next_run_at <= int(moment),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in due:
            try:
                following = next_fire_after(row.cron, row.timezone, moment)
            except MissionError:
                following = None  # a schedule that no longer validates is paused, not retried
            result = await session.execute(
                update(ScheduledMissionModel)
                .where(
                    ScheduledMissionModel.id == row.id,
                    ScheduledMissionModel.next_run_at == row.next_run_at,
                )
                .values(next_run_at=following, enabled=following is not None)
            )
            if getattr(result, "rowcount", 0):
                claimed.append(_to_mission(row))
    return claimed
