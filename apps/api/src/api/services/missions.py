"""Missions: agent runs the platform starts on its own (``specifications.interfaces.automation``).

A mission is owned like any shared object (a member, a team or the organization) and runs as
an ordinary session. A personal mission runs as its owner; a team's or the organization's runs as
its own service member (``mission_<id>``), a member of the organization (and of the team) with
the plain member role, so it can do what the scope lets any member do and nothing a person who
merely triggered it could add. Scheduling is conservative: a minimum interval, caps per member
and per organization, and an atomic claim so a firing starts exactly one run however many API
replicas are running.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jsonschema
from agent_core.tools.safe_http import UnsafeURLError, resolve_public_target
from croniter import croniter
from sqlalchemy import select, update

from api.db.models import MissionModel
from api.db.session import DatabaseSessionManager
from api.services.ownership import owner_of
from api.services.teams import note_memory_owned
from specifications.interfaces.automation import (
    DELIVERY_KINDS,
    ITEM_DELIVERIES,
    MAX_DELIVERIES,
    MAX_OUTPUT_SCHEMA_CHARS,
    MAX_TRIGGERS,
    MISSION_MODES,
    MODEL_TIERS,
    REVIEW_SOURCES,
    TRIGGER_SOURCES,
    Delivery,
    Mission,
    MissionTrigger,
    TriggerEvent,
    TriggerFilter,
    matches,
)
from specifications.interfaces.ownership import Viewer, can_see

MIN_INTERVAL_SECONDS = 300
MAX_MISSIONS_PER_USER = 20
MAX_MISSIONS_PER_ORG = 200
MAX_INSTRUCTIONS_CHARS = 20_000
SERVICE_MEMBER_PREFIX = "mission_"
# Sources whose events name an item (a pull request, an issue) a delivery can post on.
_ITEM_SOURCES = frozenset(
    {
        "github_pr",
        "github_issue_label",
        "github_issue_comment",
        "github_pr_review_comment",
    }
)
_SLACK_CHANNEL = re.compile(r"^[CGD][A-Z0-9]{2,31}$")


class MissionError(ValueError):
    """The mission is not acceptable as submitted."""


# -- schedules ----------------------------------------------------------------------------


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
    iterator = croniter(cron, datetime.fromtimestamp(after, tz=zone))
    first = iterator.get_next(datetime)
    second = iterator.get_next(datetime)
    if (second - first).total_seconds() < MIN_INTERVAL_SECONDS:
        raise MissionError("A mission may not run more often than every 5 minutes.")
    return int(first.astimezone(UTC).timestamp())


def next_run_of(mission: Mission, after: float) -> int | None:
    """The earliest firing of the mission's schedule triggers, or ``None`` (none, or paused)."""
    if not mission.enabled:
        return None
    fires = [
        next_fire_after(t.schedule, t.timezone, after)
        for t in mission.triggers
        if t.source == "schedule"
    ]
    return min(fires) if fires else None


# -- validation ----------------------------------------------------------------------------


def _check_triggers(mission: Mission) -> None:
    if len(mission.triggers) > MAX_TRIGGERS:
        raise MissionError(f"A mission has at most {MAX_TRIGGERS} triggers.")
    for trigger in mission.triggers:
        if trigger.source not in TRIGGER_SOURCES:
            raise MissionError(f"Unknown trigger source '{trigger.source}'.")
        if trigger.source == "schedule":
            next_fire_after(trigger.schedule, trigger.timezone, time.time())
    if mission.mode not in MISSION_MODES:
        raise MissionError(f"Unknown mode '{mission.mode}'.")
    if mission.mode == "review" and not any(t.source in REVIEW_SOURCES for t in mission.triggers):
        raise MissionError("A review mission needs a pull request (merge request) trigger.")
    if mission.model_tier not in MODEL_TIERS:
        raise MissionError(f"Unknown model tier '{mission.model_tier}'.")


def _check_output_schema(schema: dict[str, Any]) -> None:
    if not schema:
        return
    if len(str(schema)) > MAX_OUTPUT_SCHEMA_CHARS:
        raise MissionError(
            f"The output schema is limited to {MAX_OUTPUT_SCHEMA_CHARS:,} characters: the agent "
            "reads it on every step of the run."
        )
    if schema.get("type") != "object":
        raise MissionError('The output schema describes an object (`"type": "object"`).')
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as err:
        raise MissionError(f"The output schema is not valid JSON Schema: {err.message}") from err


def _check_delivery(mission: Mission, delivery: Delivery) -> None:
    if delivery.kind not in DELIVERY_KINDS:
        raise MissionError(f"Unknown delivery '{delivery.kind}'.")
    if delivery.kind == "chat" and not _SLACK_CHANNEL.match(delivery.channel):
        raise MissionError("A chat delivery names a Slack channel id (C..., G... or D...).")
    if delivery.kind == "webhook":
        if not delivery.url.startswith("https://"):
            raise MissionError("A webhook delivery sends to an https URL.")
        _check_output_schema(delivery.output_schema)
    if delivery.kind in ITEM_DELIVERIES and not any(
        t.source in _ITEM_SOURCES for t in mission.triggers
    ):
        raise MissionError(
            "A GitHub review or comment is posted on the pull request or issue the event is "
            "about: the mission needs a GitHub trigger."
        )
    if delivery.kind == "github_review" and not any(
        t.source == "github_pr" for t in mission.triggers
    ):
        raise MissionError("A GitHub review needs a pull request trigger.")


def validate(mission: Mission) -> Mission:
    """The mission as stored, or ``MissionError`` naming what is wrong."""
    name = " ".join(mission.name.split())
    if not name:
        raise MissionError("A mission needs a name.")
    if len(mission.instructions) > MAX_INSTRUCTIONS_CHARS:
        raise MissionError(f"Instructions are limited to {MAX_INSTRUCTIONS_CHARS:,} characters.")
    _check_triggers(mission)
    kinds = [d.kind for d in mission.deliveries]
    if len(kinds) > MAX_DELIVERIES or len(set(kinds)) != len(kinds):
        raise MissionError(f"At most {MAX_DELIVERIES} deliveries, one of each kind.")
    for delivery in mission.deliveries:
        _check_delivery(mission, delivery)
    deliveries = mission.deliveries
    if "session" not in kinds:
        deliveries = (Delivery("session"), *deliveries)
    return replace(mission, name=name[:120], deliveries=deliveries)


async def check_webhook_urls(mission: Mission) -> None:
    """A webhook delivery's URL must resolve to public addresses (pinned again at each send)."""
    for delivery in mission.deliveries:
        if delivery.kind != "webhook":
            continue
        try:
            await resolve_public_target(delivery.url)
        except UnsafeURLError as err:
            raise MissionError(f"Webhook URL refused: {err}") from err


# -- rows -----------------------------------------------------------------------------------


def _trigger(raw: dict[str, Any]) -> MissionTrigger:
    rules = raw.get("filter") if isinstance(raw.get("filter"), dict) else {}
    return MissionTrigger(
        source=str(raw.get("source") or ""),
        filter=TriggerFilter(
            **{
                key: tuple(str(v) for v in (rules or {}).get(key) or ())
                for key in TriggerFilter.__dataclass_fields__
            }
        ),
        schedule=str(raw.get("schedule") or ""),
        timezone=str(raw.get("timezone") or "UTC"),
    )


def _delivery(raw: dict[str, Any]) -> Delivery:
    kind = str(raw.get("kind") or "session")
    return Delivery(
        kind=next((k for k in DELIVERY_KINDS if k == kind), "session"),
        bot_id=str(raw.get("bot_id") or ""),
        channel=str(raw.get("channel") or ""),
        url=str(raw.get("url") or ""),
        output_schema=dict(raw.get("output_schema") or {}),
    )


def triggers_json(mission: Mission) -> list[dict[str, Any]]:
    return [
        {**asdict(t), "filter": {k: list(v) for k, v in asdict(t.filter).items()}}
        for t in mission.triggers
    ]


def deliveries_json(mission: Mission) -> list[dict[str, Any]]:
    return [
        {k: v for k, v in asdict(d).items() if v not in ("", {}, None)} for d in mission.deliveries
    ]


_SCALARS = (
    "id", "org_id", "scope", "user_id", "team_id", "name", "instructions", "agent_id",
    "permission_mode", "model_tier", "sandbox_type", "mode", "auto_fix", "git_repo",
    "git_branch", "enabled", "run_as", "webhook_fingerprint", "next_run_at", "last_run_at",
    "last_session_id", "last_status", "created_by", "created_at", "updated_at",
)  # fmt: skip


def from_row(row: MissionModel | dict[str, Any]) -> Mission:
    def field(name: str) -> Any:
        return row.get(name) if isinstance(row, dict) else getattr(row, name)

    return Mission(
        **{name: field(name) for name in _SCALARS},
        triggers=tuple(_trigger(t) for t in field("triggers") or [] if isinstance(t, dict)),
        deliveries=tuple(_delivery(d) for d in field("deliveries") or [] if isinstance(d, dict)),
    )


def _columns(mission: Mission) -> dict[str, Any]:
    return {
        **{name: getattr(mission, name) for name in _SCALARS},
        "triggers": triggers_json(mission),
        "deliveries": deliveries_json(mission),
    }


# -- the store -------------------------------------------------------------------------------


class MissionStore:
    """Missions in PostgreSQL (tenant-scoped); a process-local dict without a database.

    ``secrets`` (the inbound webhook's and a webhook delivery's, sealed) are kept beside the
    mission and never leave through ``Mission``."""

    def __init__(self, db_manager: DatabaseSessionManager | None) -> None:
        self.bound_to = db_manager
        self._memory: dict[str, tuple[Mission, dict[str, str]]] = {}

    @property
    def _db(self) -> DatabaseSessionManager | None:
        # Decided at each call, like the request-scoped stores: the store lives as long as the
        # app, and a manager that was closed since has no session factory any more.
        manager = self.bound_to
        return (
            manager if manager is not None and getattr(manager, "session_factory", None) else None
        )

    async def _all(self, org_id: str) -> list[Mission]:
        if self._db is None:
            return [m for m, _ in self._memory.values() if m.org_id == org_id]
        async with self._db.session(tenant_org_id=org_id) as session:
            rows = await session.execute(select(MissionModel).where(MissionModel.org_id == org_id))
            return [from_row(row) for row in rows.scalars()]

    async def all_for(self, org_id: str, viewer: Viewer | None = None) -> list[Mission]:
        """The organization's missions (``viewer``: only those they may see), oldest first."""
        found = await self._all(org_id)
        if viewer is not None:
            found = [m for m in found if can_see(viewer, owner_of(m))]
        return sorted(found, key=lambda m: m.created_at)

    async def get(self, org_id: str, mission_id: str) -> Mission | None:
        if self._db is None:
            held = self._memory.get(mission_id)
            return held[0] if held and held[0].org_id == org_id else None
        async with self._db.session(tenant_org_id=org_id) as session:
            row = await session.get(MissionModel, mission_id)
            return from_row(row) if row is not None and row.org_id == org_id else None

    async def secrets(self, org_id: str, mission_id: str) -> dict[str, str]:
        if self._db is None:
            held = self._memory.get(mission_id)
            return dict(held[1]) if held and held[0].org_id == org_id else {}
        async with self._db.session(tenant_org_id=org_id) as session:
            row = await session.get(MissionModel, mission_id)
            return dict(row.secrets or {}) if row is not None and row.org_id == org_id else {}

    async def _enforce_caps(self, mission: Mission) -> None:
        missions = await self._all(mission.org_id)
        if len(missions) >= MAX_MISSIONS_PER_ORG:
            raise MissionError("This organization has reached its mission limit.")
        mine = [m for m in missions if m.scope == "user" and m.user_id == mission.user_id]
        if mission.scope == "user" and len(mine) >= MAX_MISSIONS_PER_USER:
            raise MissionError("You have reached your mission limit.")

    async def create(self, mission: Mission, secrets: dict[str, str]) -> Mission:
        now = int(time.time())
        mission = validate(
            replace(mission, id=f"mission_{uuid.uuid4().hex[:10]}", created_at=now, updated_at=now)
        )
        mission.next_run_at = next_run_of(mission, now)
        await self._enforce_caps(mission)
        if self._db is None:
            self._memory[mission.id] = (mission, dict(secrets))
            _note_team(mission, 1)
            return mission
        async with self._db.session(tenant_org_id=mission.org_id) as session:
            session.add(MissionModel(**_columns(mission), secrets=dict(secrets)))
        return mission

    async def save(self, mission: Mission, secrets: dict[str, str] | None = None) -> Mission:
        """Replace a stored mission (``secrets`` ``None`` keeps the stored ones)."""
        before = await self.get(mission.org_id, mission.id)
        mission = validate(replace(mission, updated_at=int(time.time())))
        if before is None or _schedule_changed(before, mission):
            mission.next_run_at = next_run_of(mission, time.time())
        if self._db is None:
            kept = self._memory.get(mission.id, (mission, {}))[1]
            if before is not None:
                _note_team(before, -1)
            self._memory[mission.id] = (mission, dict(secrets if secrets is not None else kept))
            _note_team(mission, 1)
            return mission
        async with self._db.session(tenant_org_id=mission.org_id) as session:
            values = _columns(mission)
            if secrets is not None:
                values["secrets"] = dict(secrets)
            await session.execute(
                update(MissionModel)
                .where(MissionModel.id == mission.id, MissionModel.org_id == mission.org_id)
                .values(**{k: v for k, v in values.items() if k not in ("id", "org_id")})
            )
        return mission

    async def mark_run(
        self, org_id: str, mission_id: str, session_id: str | None, status: str
    ) -> None:
        """Remember the latest run (and its session) so the page can link to it."""
        now = int(time.time())
        if self._db is None:
            held = self._memory.get(mission_id)
            if held is not None:
                ran = replace(
                    held[0], last_run_at=now, last_session_id=session_id, last_status=status
                )
                self._memory[mission_id] = (ran, held[1])
            return
        async with self._db.session(tenant_org_id=org_id) as session:
            await session.execute(
                update(MissionModel)
                .where(MissionModel.id == mission_id)
                .values(last_run_at=now, last_session_id=session_id, last_status=status)
            )

    async def delete(self, org_id: str, mission_id: str) -> bool:
        if self._db is None:
            held = self._memory.get(mission_id)
            if held is None or held[0].org_id != org_id:
                return False
            del self._memory[mission_id]
            _note_team(held[0], -1)
            return True
        async with self._db.session(tenant_org_id=org_id) as session:
            row = await session.get(MissionModel, mission_id)
            if row is None or row.org_id != org_id:
                return False
            await session.delete(row)
        return True

    async def claim_due(self, admin_db: Any, now: float | None = None) -> list[Mission]:
        """Missions whose schedule has come, each claimed by moving ``next_run_at`` forward
        atomically. Runs on the schema-owner connection (it must see every tenant); a replica
        that loses the race for a row gets ``rowcount == 0`` and skips it."""
        moment = now if now is not None else time.time()
        if admin_db is None:
            return self._claim_in_memory(moment)
        claimed: list[Mission] = []
        async with admin_db.session() as session:
            due = await session.execute(
                select(MissionModel).where(
                    MissionModel.enabled.is_(True), MissionModel.next_run_at <= int(moment)
                )
            )
            for row in due.scalars().all():
                mission = from_row(row)
                try:
                    following = next_run_of(mission, moment)
                except MissionError:
                    following = None  # a schedule that no longer validates is paused
                result = await session.execute(
                    update(MissionModel)
                    .where(MissionModel.id == row.id, MissionModel.next_run_at == row.next_run_at)
                    .values(next_run_at=following, enabled=following is not None)
                )
                if getattr(result, "rowcount", 0):
                    claimed.append(mission)
        return claimed

    def _claim_in_memory(self, moment: float) -> list[Mission]:
        claimed: list[Mission] = []
        for mission_id, (mission, secrets) in list(self._memory.items()):
            if (
                mission.enabled
                and mission.next_run_at is not None
                and mission.next_run_at <= moment
            ):
                mission.next_run_at = next_run_of(mission, moment)
                self._memory[mission_id] = (mission, secrets)
                claimed.append(mission)
        return claimed

    async def matching(self, org_id: str, event: TriggerEvent) -> list[Mission]:
        """The organization's enabled missions with a trigger that takes ``event``."""
        return [
            m
            for m in await self._all(org_id)
            if m.enabled and any(matches(t, event) for t in m.triggers)
        ]


def _schedule_changed(before: Mission, after: Mission) -> bool:
    def schedule(m: Mission) -> list[tuple[str, str]]:
        return sorted((t.schedule, t.timezone) for t in m.triggers if t.source == "schedule")

    return schedule(before) != schedule(after) or before.enabled != after.enabled


def _note_team(mission: Mission, delta: int) -> None:
    if mission.team_id:
        note_memory_owned(mission.org_id, mission.team_id, "missions", delta)


def service_member_id(mission_id: str) -> str:
    """The member a shared mission runs as: stable, and never a person's id."""
    digest = hashlib.sha256(mission_id.encode()).hexdigest()[:16]
    return f"{SERVICE_MEMBER_PREFIX}{digest}"


def is_service_member(user_id: str) -> bool:
    return user_id.startswith(SERVICE_MEMBER_PREFIX)
