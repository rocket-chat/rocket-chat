"""Spend limits: what they are, which apply to a call, and the windows they count in.

A limit caps the dollars one *subject* spends over one *period*:

* subjects - the organization, a member (or each member), an agent, an agent for a member
  (or for each member), a Slack workspace;
* periods - calendar ``day``, ``week``, ``month`` (UTC), ``rolling_24h`` and ``rolling_7d``
  (hourly buckets summed over the window), one ``session``, one ``turn``;
* modes - ``block`` refuses the call that would cross it, ``warn`` only tells the member.

A row with no member (``member`` with no ``subject_id``, ``agent_member`` with no
``member_id``) is the default for each member; an explicit row for a member replaces it, and
when the default is marked ``is_ceiling`` the explicit row can only lower it.

Calls paid with a member's own provider key count against no limit: that money is theirs.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import select

from api.db.models import SpendLimitModel
from specifications.interfaces.llm import ModelScope

SubjectKind = Literal["org", "member", "agent", "agent_member", "slack_workspace"]
Period = Literal["day", "week", "month", "rolling_24h", "rolling_7d", "session", "turn"]
Mode = Literal["warn", "block"]

SUBJECT_KINDS: tuple[SubjectKind, ...] = (
    "org",
    "member",
    "agent",
    "agent_member",
    "slack_workspace",
)
PERIODS: tuple[Period, ...] = (
    "day",
    "week",
    "month",
    "rolling_24h",
    "rolling_7d",
    "session",
    "turn",
)
MODES: tuple[Mode, ...] = ("warn", "block")
ROLLING_HOURS = {"rolling_24h": 24, "rolling_7d": 168}
HOUR = 3600
# Calls on these keys are the organization's money and count against limits.
LIMITED_KEY_SOURCES = ("platform", "org", "agent")


@dataclass(frozen=True)
class SpendLimit:
    id: str
    org_id: str
    subject_kind: SubjectKind
    subject_id: str | None
    member_id: str | None
    period: Period
    amount_usd: float
    mode: Mode
    warn_at_percent: int
    is_ceiling: bool
    created_by: str
    created_at: int
    updated_at: int

    @property
    def is_default_for_each(self) -> bool:
        if self.subject_kind == "member":
            return self.subject_id is None
        return self.subject_kind == "agent_member" and self.member_id is None


@dataclass(frozen=True)
class AppliedLimit:
    """A limit as it applies to one call: the counter it counts in and the amount it allows."""

    limit: SpendLimit
    subject_key: str
    amount_usd: float


@dataclass(frozen=True)
class Window:
    """Where a period counts now: the bucket to add to, and the buckets to sum."""

    current: int
    since: int
    resets_at: int | None


def _one_of(value: str, allowed: tuple[str, ...], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"stored spend limit has an unknown {field} {value!r}")
    return value


def _limit(row: SpendLimitModel) -> SpendLimit:
    return SpendLimit(
        id=row.id,
        org_id=row.org_id,
        subject_kind=cast(SubjectKind, _one_of(row.subject_kind, SUBJECT_KINDS, "subject")),
        subject_id=row.subject_id,
        member_id=row.member_id,
        period=cast(Period, _one_of(row.period, PERIODS, "period")),
        amount_usd=float(row.amount_usd),
        mode=cast(Mode, _one_of(row.mode, MODES, "mode")),
        warn_at_percent=int(row.warn_at_percent),
        is_ceiling=bool(row.is_ceiling),
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def window(period: Period, now: float | None = None) -> Window:
    """The counting window of ``period`` at ``now`` (UTC calendar periods)."""
    moment = datetime.fromtimestamp(now if now is not None else time.time(), tz=UTC)
    if period in ("session", "turn"):
        return Window(0, 0, None)
    if period in ROLLING_HOURS:
        hour = int(moment.timestamp()) // HOUR * HOUR
        hours = ROLLING_HOURS[period]
        return Window(hour, hour - (hours - 1) * HOUR, hour + HOUR)
    day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return Window(
            int(day.timestamp()), int(day.timestamp()), int((day + timedelta(days=1)).timestamp())
        )
    if period == "week":
        start = day - timedelta(days=day.weekday())
        return Window(
            int(start.timestamp()),
            int(start.timestamp()),
            int((start + timedelta(days=7)).timestamp()),
        )
    start = day.replace(day=1)
    following = (start + timedelta(days=32)).replace(day=1)
    return Window(int(start.timestamp()), int(start.timestamp()), int(following.timestamp()))


def slack_team_of(member_id: str) -> str | None:
    """The Slack team of a Slack guest (``slack:<team>:<user>`` or ``slack:<team>``)."""
    if not member_id.startswith("slack:"):
        return None
    parts = member_id.split(":")
    return parts[1] if len(parts) > 1 and parts[1] else None


def _base_key(limit: SpendLimit, scope: ModelScope) -> str | None:
    """The counter a limit counts this call in, or ``None`` when it does not apply."""
    member = scope.member_id
    if limit.subject_kind == "org":
        return "org"
    if limit.subject_kind == "member":
        return member if limit.subject_id in (None, member) else None
    if limit.subject_kind == "agent":
        return scope.agent_id if scope.agent_id and limit.subject_id == scope.agent_id else None
    if limit.subject_kind == "agent_member":
        if not scope.agent_id or limit.subject_id != scope.agent_id:
            return None
        return f"{scope.agent_id}:{member}" if limit.member_id in (None, member) else None
    team = slack_team_of(member)
    return team if team and limit.subject_id == team else None


def _keyed(limit: SpendLimit, base: str, scope: ModelScope) -> str | None:
    if limit.period == "session":
        return f"{base}|session:{scope.session_id}" if scope.session_id else None
    if limit.period == "turn":
        return f"{base}|turn:{scope.turn_id}" if scope.turn_id else None
    return base


def applicable(limits: list[SpendLimit], scope: ModelScope, key_source: str) -> list[AppliedLimit]:
    """The limits one call counts against, with explicit member rows replacing defaults."""
    if key_source not in LIMITED_KEY_SOURCES:
        return []
    applied: list[AppliedLimit] = []
    for limit in limits:
        base = _base_key(limit, scope)
        key = _keyed(limit, base, scope) if base is not None else None
        if key is not None:
            applied.append(AppliedLimit(limit, key, limit.amount_usd))
    return _resolve_defaults(applied)


def _resolve_defaults(applied: list[AppliedLimit]) -> list[AppliedLimit]:
    """An explicit member row replaces the default-for-each of the same subject, period and
    mode; a default marked as a ceiling still caps it."""

    def group(item: AppliedLimit) -> tuple[str, str, str]:
        return item.limit.subject_kind, item.limit.period, item.limit.mode

    per_member = ("member", "agent_member")
    explicit = {
        group(a): a
        for a in applied
        if a.limit.subject_kind in per_member and not a.limit.is_default_for_each
    }
    defaults = {group(a): a for a in applied if a.limit.is_default_for_each}
    kept = [a for a in applied if a.limit.subject_kind not in per_member]
    kept.extend(default for key, default in defaults.items() if key not in explicit)
    for key, chosen in explicit.items():
        default = defaults.get(key)
        if default is not None and default.limit.is_ceiling:
            chosen = replace(chosen, amount_usd=min(chosen.amount_usd, default.amount_usd))
        kept.append(chosen)
    return kept


def describe(limit: SpendLimit) -> str:
    """ "Agent Claude-Pro, per day" - the limit's subject and period for people."""
    period = {
        "day": "per day",
        "week": "per week",
        "month": "per month",
        "rolling_24h": "per 24 hours",
        "rolling_7d": "per 7 days",
        "session": "per session",
        "turn": "per turn",
    }[limit.period]
    subject = {
        "org": "Organization",
        "member": "Each member" if limit.subject_id is None else "Member",
        "agent": "Agent",
        "agent_member": "Agent, each member" if limit.member_id is None else "Agent for a member",
        "slack_workspace": "Slack workspace",
    }[limit.subject_kind]
    return f"{subject}, {period}"


class SpendLimitStore:
    """The organization's limits, in PostgreSQL (forced RLS) or in process memory."""

    def __init__(self, db_mgr: Any) -> None:
        self._db = (
            db_mgr if db_mgr is not None and getattr(db_mgr, "session_factory", None) else None
        )
        self._memory: dict[str, SpendLimit] = {}

    async def list(self, org_id: str) -> list[SpendLimit]:
        if self._db is None:
            found = [item for item in self._memory.values() if item.org_id == org_id]
        else:
            async with self._db.session(tenant_org_id=org_id) as session:
                rows = await session.execute(
                    select(SpendLimitModel).where(SpendLimitModel.org_id == org_id)
                )
                found = [_limit(row) for row in rows.scalars()]
        return sorted(found, key=lambda item: (item.subject_kind, item.period, item.created_at))

    async def get(self, org_id: str, limit_id: str) -> SpendLimit | None:
        return next((item for item in await self.list(org_id) if item.id == limit_id), None)

    async def create(self, limit: SpendLimit) -> SpendLimit:
        created = replace(limit, id=limit.id or f"lim_{uuid.uuid4().hex[:16]}")
        if self._db is None:
            self._memory[created.id] = created
            return created
        async with self._db.session(tenant_org_id=created.org_id) as session:
            session.add(
                SpendLimitModel(
                    id=created.id,
                    org_id=created.org_id,
                    subject_kind=created.subject_kind,
                    subject_id=created.subject_id,
                    member_id=created.member_id,
                    period=created.period,
                    amount_usd=created.amount_usd,
                    mode=created.mode,
                    warn_at_percent=created.warn_at_percent,
                    is_ceiling=created.is_ceiling,
                    created_by=created.created_by,
                    created_at=created.created_at,
                    updated_at=created.updated_at,
                )
            )
        return created

    async def update(self, org_id: str, limit_id: str, **changes: Any) -> SpendLimit | None:
        now = int(time.time())
        if self._db is None:
            current = self._memory.get(limit_id)
            if current is None or current.org_id != org_id:
                return None
            self._memory[limit_id] = replace(current, **changes, updated_at=now)
            return self._memory[limit_id]
        async with self._db.session(tenant_org_id=org_id) as session:
            row = await session.get(SpendLimitModel, limit_id)
            if row is None or row.org_id != org_id:
                return None
            for name, value in changes.items():
                setattr(row, name, value)
            row.updated_at = now
            return _limit(row)

    async def delete(self, org_id: str, limit_id: str) -> SpendLimit | None:
        if self._db is None:
            current = self._memory.get(limit_id)
            if current is None or current.org_id != org_id:
                return None
            del self._memory[limit_id]
            return current
        async with self._db.session(tenant_org_id=org_id) as session:
            row = await session.get(SpendLimitModel, limit_id)
            if row is None or row.org_id != org_id:
                return None
            removed = _limit(row)
            await session.delete(row)
            return removed
