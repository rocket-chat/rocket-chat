"""The persisted usage ledger.

Every model call becomes a ``usage_events`` row. Budgets, the usage dashboards and the Slack
workspace spend are all computed from it, so they survive restarts and agree across replicas.
Without a database (development) an in-process list stands in.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from api.db.models import UsageEventModel
from specifications.interfaces.llm import UsageEvent

# Calls paid with the organization's money (a key bound to an agent is the organization's or
# a member's shared key: it counts against that agent's limits, like an organization key).
PLATFORM_FUNDED = ("platform", "org", "agent")
GROUPINGS = ("agent", "credential", "member")
UNATTRIBUTED = "unattributed"


def month_start(now: float | None = None) -> int:
    """Epoch seconds of 00:00 UTC on the first day of the current month."""
    moment = datetime.fromtimestamp(now if now is not None else time.time(), tz=UTC)
    return int(moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())


class UsageLedger:
    """Append model calls and total them by organization, member or session."""

    def __init__(self, db_mgr: Any = None) -> None:
        self._db = (
            db_mgr if db_mgr is not None and getattr(db_mgr, "session_factory", None) else None
        )
        self._memory: list[UsageEvent] = []

    async def record(self, event: UsageEvent) -> None:
        if self._db is None:
            self._memory.append(event)
            return
        async with self._db.session(tenant_org_id=event.org_id) as session:
            session.add(
                UsageEventModel(
                    id=f"use_{uuid.uuid4().hex[:20]}",
                    org_id=event.org_id,
                    user_id=event.user_id,
                    session_id=event.session_id,
                    model=event.model,
                    prompt_tokens=event.prompt_tokens,
                    completion_tokens=event.completion_tokens,
                    cache_read_tokens=event.cache_read_tokens,
                    cache_creation_tokens=event.cache_creation_tokens,
                    cost_usd=event.cost_usd,
                    key_source=event.key_source,
                    created_at=event.created_at,
                    agent_id=event.agent_id,
                    credential_id=event.credential_id,
                    turn_id=event.turn_id,
                    subagent_id=event.subagent_id,
                    call_role=event.call_role or None,
                    requester_user_id=event.requester_user_id,
                )
            )

    async def spend_usd(
        self,
        org_id: str,
        *,
        since: int,
        user_id: str | None = None,
        session_id: str | None = None,
        session_prefix: str | None = None,
        platform_funded_only: bool = True,
    ) -> float:
        """Total cost since ``since``.

        ``platform_funded_only`` leaves out calls paid with a member's own provider key:
        that money is theirs, not the organization's budget.
        """
        if self._db is None:
            return round(
                sum(
                    e.cost_usd
                    for e in self._memory
                    if e.org_id == org_id
                    and e.created_at >= since
                    and (user_id is None or e.user_id == user_id)
                    and (session_id is None or e.session_id == session_id)
                    and (session_prefix is None or (e.session_id or "").startswith(session_prefix))
                    and (not platform_funded_only or e.key_source in PLATFORM_FUNDED)
                ),
                6,
            )
        statement = select(func.coalesce(func.sum(UsageEventModel.cost_usd), 0.0)).where(
            UsageEventModel.org_id == org_id, UsageEventModel.created_at >= since
        )
        if user_id is not None:
            statement = statement.where(UsageEventModel.user_id == user_id)
        if session_id is not None:
            statement = statement.where(UsageEventModel.session_id == session_id)
        if session_prefix is not None:
            statement = statement.where(UsageEventModel.session_id.startswith(session_prefix))
        if platform_funded_only:
            statement = statement.where(UsageEventModel.key_source.in_(PLATFORM_FUNDED))
        async with self._db.session(tenant_org_id=org_id) as session:
            return round(float(await session.scalar(statement) or 0.0), 6)

    async def summary(
        self, org_id: str, *, since: int, user_id: str | None = None
    ) -> dict[str, Any]:
        """Tokens and cost since ``since``, in total and per model (own-key calls included)."""
        rows = await self._rows(org_id, since, user_id)
        by_model: dict[str, dict[str, Any]] = {}
        for row in rows:
            entry = by_model.setdefault(
                row["model"], {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
            )
            entry["prompt_tokens"] += row["prompt_tokens"]
            entry["completion_tokens"] += row["completion_tokens"]
            entry["cost_usd"] = round(entry["cost_usd"] + row["cost_usd"], 6)
        prompt = sum(r["prompt_tokens"] for r in rows)
        completion = sum(r["completion_tokens"] for r in rows)
        return {
            "total_tokens": prompt + completion,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "cache_read_tokens": sum(r["cache_read_tokens"] for r in rows),
            "cache_creation_tokens": sum(r["cache_creation_tokens"] for r in rows),
            "own_key_cost_usd": round(
                sum(r["cost_usd"] for r in rows if r["key_source"] == "user"), 4
            ),
            "calls": len(rows),
            "models": by_model,
        }

    async def grouped(
        self, org_id: str, *, since: int, by: str, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Cost, tokens and calls since ``since`` per agent, credential or member.

        Calls from before attribution existed have no agent or credential and are reported
        as ``unattributed``; a member is the requester, else the session's owner.
        """
        if by not in GROUPINGS:
            raise ValueError(f"group by one of {GROUPINGS}")
        totals: dict[str, dict[str, Any]] = {}
        for row in await self._attributed(org_id, since, user_id):
            key = {
                "agent": row["agent_id"],
                "credential": row["credential_id"],
                "member": row["requester_user_id"] or row["user_id"],
            }[by] or UNATTRIBUTED
            entry = totals.setdefault(
                key,
                {
                    "key": key,
                    "cost_usd": 0.0,
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                },
            )
            entry["cost_usd"] = round(entry["cost_usd"] + row["cost_usd"], 6)
            entry["calls"] += 1
            entry["prompt_tokens"] += row["prompt_tokens"]
            entry["completion_tokens"] += row["completion_tokens"]
        return sorted(totals.values(), key=lambda e: -e["cost_usd"])

    async def _attributed(
        self, org_id: str, since: int, user_id: str | None
    ) -> list[dict[str, Any]]:
        fields = ("agent_id", "credential_id", "requester_user_id", "user_id")
        if self._db is None:
            return [
                {**_event_row(e), **{f: getattr(e, f, None) for f in fields}}
                for e in self._memory
                if e.org_id == org_id
                and e.created_at >= since
                and (user_id is None or (e.requester_user_id or e.user_id) == user_id)
            ]
        statement = select(UsageEventModel).where(
            UsageEventModel.org_id == org_id, UsageEventModel.created_at >= since
        )
        async with self._db.session(tenant_org_id=org_id) as session:
            rows = (await session.execute(statement)).scalars().all()
        return [
            {**_event_row(r), **{f: getattr(r, f, None) for f in fields}}
            for r in rows
            if user_id is None or (r.requester_user_id or r.user_id) == user_id
        ]

    async def session_events(self, org_id: str, session_id: str) -> list[dict[str, Any]]:
        """Every call of one session, oldest first, as plain rows."""
        if self._db is None:
            return sorted(
                (
                    _event_row(e)
                    for e in self._memory
                    if e.org_id == org_id and e.session_id == session_id
                ),
                key=lambda row: row["created_at"],
            )
        statement = (
            select(UsageEventModel)
            .where(UsageEventModel.org_id == org_id, UsageEventModel.session_id == session_id)
            .order_by(UsageEventModel.created_at)
        )
        async with self._db.session(tenant_org_id=org_id) as session:
            return [_event_row(r) for r in (await session.execute(statement)).scalars()]

    async def _rows(self, org_id: str, since: int, user_id: str | None) -> list[dict[str, Any]]:
        if self._db is None:
            return [
                {
                    "model": e.model,
                    "prompt_tokens": e.prompt_tokens,
                    "completion_tokens": e.completion_tokens,
                    "cache_read_tokens": e.cache_read_tokens,
                    "cache_creation_tokens": e.cache_creation_tokens,
                    "cost_usd": e.cost_usd,
                    "key_source": e.key_source,
                }
                for e in self._memory
                if e.org_id == org_id
                and e.created_at >= since
                and (user_id is None or e.user_id == user_id)
            ]
        statement = select(UsageEventModel).where(
            UsageEventModel.org_id == org_id, UsageEventModel.created_at >= since
        )
        if user_id is not None:
            statement = statement.where(UsageEventModel.user_id == user_id)
        async with self._db.session(tenant_org_id=org_id) as session:
            return [
                {
                    "model": r.model,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "cache_read_tokens": r.cache_read_tokens,
                    "cache_creation_tokens": r.cache_creation_tokens,
                    "cost_usd": r.cost_usd,
                    "key_source": r.key_source,
                }
                for r in (await session.execute(statement)).scalars()
            ]


def _event_row(event: Any) -> dict[str, Any]:
    """A ledger call, from the stored row or the in-process event, as the plain row totals use."""
    return {
        "model": event.model,
        "prompt_tokens": event.prompt_tokens,
        "completion_tokens": event.completion_tokens,
        "cache_read_tokens": event.cache_read_tokens,
        "cache_creation_tokens": event.cache_creation_tokens,
        "cost_usd": event.cost_usd,
        "key_source": event.key_source,
        "created_at": event.created_at,
    }
