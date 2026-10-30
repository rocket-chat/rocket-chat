"""Spend limits enforced before a model call is made, by reservation.

Checking the ledger before each call and writing it after lets parallel calls all pass the
check and overshoot together. Instead, before a call the gateway reserves its estimated cost
(prompt tokens plus ``max_tokens`` at the model's price) on every limit that applies:

* on PostgreSQL, one transaction runs a conditional ``UPDATE ... WHERE spent + reserved +
  estimate <= amount RETURNING`` per block limit; the row lock serialises concurrent calls,
  and a miss rolls every reservation of the call back and raises ``BudgetExceededError``;
* without a database, the same arithmetic runs under one lock.

After the call (``settle``) the estimate leaves ``reserved_usd`` and the real cost joins
``spent_usd``. So concurrent calls overshoot a block limit by at most one estimate's error,
never by the number of calls. A reservation whose call never settled (a crash) is released by
``release_stale`` after ``STALE_RESERVATION_SECONDS``.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db.models import SpendCounterModel, SpendReservationModel
from api.services.spend_limits import (
    AppliedLimit,
    SpendLimit,
    SpendLimitStore,
    applicable,
    describe,
    window,
)
from api.services.usage import UsageLedger
from specifications.interfaces.llm import BudgetExceededError, ModelScope, Reservation

# Limits change rarely and are read before every model call; a short cache keeps that off the
# database. A replica notices an edit within this window (``forget`` clears it at once here).
SETTINGS_TTL_SECONDS = 15.0
STALE_RESERVATION_SECONDS = 15 * 60

Counter = tuple[str, str, int]  # (limit id, subject key, window start)


@dataclass(frozen=True)
class LimitStatus:
    """How much of one limit this caller has used."""

    limit: SpendLimit
    amount_usd: float
    spent_usd: float
    resets_at: int | None

    @property
    def percent(self) -> float:
        return self.spent_usd / self.amount_usd * 100 if self.amount_usd > 0 else 0.0

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.amount_usd - self.spent_usd)

    @property
    def label(self) -> str:
        return describe(self.limit)

    def payload(self) -> dict[str, Any]:
        return {
            "limit_id": self.limit.id,
            "subject": self.limit.subject_kind,
            "subject_id": self.limit.subject_id or "",
            "period": self.limit.period,
            "mode": self.limit.mode,
            "label": self.label,
            "spent": round(self.spent_usd, 4),
            "amount": round(self.amount_usd, 4),
            "resets_at": self.resets_at,
        }


@dataclass
class BudgetStatus:
    limits: list[LimitStatus]

    def warnings(self) -> list[LimitStatus]:
        return [
            item
            for item in self.limits
            if item.amount_usd > 0 and item.limit.warn_at_percent <= item.percent
        ]

    def tightest(self) -> LimitStatus | None:
        """The limit with the least room left (block limits first)."""
        if not self.limits:
            return None
        return min(self.limits, key=lambda s: (s.limit.mode != "block", s.remaining_usd))


def exceeded(item: AppliedLimit, spent: float, resets_at: int | None) -> BudgetExceededError:
    when = (
        time.strftime(" It resets at %Y-%m-%d %H:%M UTC.", time.gmtime(resets_at))
        if resets_at
        else ""
    )
    return BudgetExceededError(
        f"Spend limit reached ({describe(item.limit)}): ${spent:.2f} of "
        f"${item.amount_usd:.2f}.{when} An administrator can raise it.",
        scope=describe(item.limit),
        limit_usd=item.amount_usd,
        spent_usd=spent,
        limit_id=item.limit.id,
        subject=item.limit.subject_kind,
        period=item.limit.period,
        resets_at=resets_at,
    )


class _MemoryCounters:
    """Counters without a database: the same rule under one lock."""

    def __init__(self) -> None:
        self.values: dict[Counter, list[float]] = {}
        self.reservations: dict[str, tuple[Reservation, int]] = {}
        self.lock = asyncio.Lock()

    def used(self, limit_id: str, key: str, since: int, until: int) -> float:
        return sum(
            spent + reserved
            for (lid, k, start), (spent, reserved) in self.values.items()
            if lid == limit_id and k == key and since <= start <= until
        )


class BudgetService:
    """Implements ``BudgetGuardProtocol`` over the organization's spend limits."""

    def __init__(
        self, db_mgr: Any, ledger: UsageLedger, store: SpendLimitStore | None = None
    ) -> None:
        self._db: Any = (
            db_mgr if db_mgr is not None and getattr(db_mgr, "session_factory", None) else None
        )
        self._ledger = ledger
        self.store = store or SpendLimitStore(db_mgr)
        self._limits: dict[str, tuple[float, list[SpendLimit]]] = {}
        self._memory = _MemoryCounters()

    async def _org_limits(self, org_id: str) -> list[SpendLimit]:
        cached = self._limits.get(org_id)
        if cached and time.monotonic() - cached[0] < SETTINGS_TTL_SECONDS:
            return cached[1]
        limits = await self.store.list(org_id)
        self._limits[org_id] = (time.monotonic(), limits)
        return limits

    def forget(self, org_id: str) -> None:
        """Drop the cached limits (called when an administrator changes them)."""
        self._limits.pop(org_id, None)

    async def applied(self, scope: ModelScope, key_source: str = "platform") -> list[AppliedLimit]:
        return applicable(await self._org_limits(scope.org_id), scope, key_source)

    # -- reservation -------------------------------------------------------------------

    async def reserve(
        self, scope: ModelScope, *, estimate_usd: float, key_source: str
    ) -> Reservation | None:
        applied = await self.applied(scope, key_source)
        if not applied:
            return None
        estimate = max(0.0, float(estimate_usd))
        now = time.time()
        if self._db is None:
            counters = await self._reserve_in_memory(applied, estimate, now)
        else:
            counters = await self._reserve_in_database(scope.org_id, applied, estimate, now)
        reservation = Reservation(
            id=f"res_{uuid.uuid4().hex[:16]}",
            org_id=scope.org_id,
            amount_usd=estimate,
            counters=tuple(counters),
        )
        await self._remember(reservation, int(now))
        return reservation

    async def _reserve_in_memory(
        self, applied: list[AppliedLimit], estimate: float, now: float
    ) -> list[Counter]:
        async with self._memory.lock:
            counters: list[Counter] = []
            for item in applied:
                span = window(item.limit.period, now)
                used = self._memory.used(item.limit.id, item.subject_key, span.since, span.current)
                if item.limit.mode == "block" and used + estimate > item.amount_usd:
                    raise exceeded(item, used, span.resets_at)
                counters.append((item.limit.id, item.subject_key, span.current))
            for counter in counters:
                self._memory.values.setdefault(counter, [0.0, 0.0])[1] += estimate
            return counters

    async def _reserve_in_database(
        self, org_id: str, applied: list[AppliedLimit], estimate: float, now: float
    ) -> list[Counter]:
        counters: list[Counter] = []
        async with self._db.session(tenant_org_id=org_id) as session:
            for item in applied:
                span = window(item.limit.period, now)
                counter = (item.limit.id, item.subject_key, span.current)
                await session.execute(
                    pg_insert(SpendCounterModel)
                    .values(
                        limit_id=counter[0],
                        subject_key=counter[1],
                        window_start=counter[2],
                        org_id=org_id,
                        spent_usd=0.0,
                        reserved_usd=0.0,
                        updated_at=int(now),
                    )
                    .on_conflict_do_nothing()
                )
                if not await self._take(session, item, counter, span.since, estimate, int(now)):
                    used = await self._used(session, counter, span.since)
                    # Raising inside the session rolls back every reservation of this call.
                    raise exceeded(item, used, span.resets_at)
                counters.append(counter)
        return counters

    @staticmethod
    def _row(counter: Counter) -> Any:
        return (
            (SpendCounterModel.limit_id == counter[0])
            & (SpendCounterModel.subject_key == counter[1])
            & (SpendCounterModel.window_start == counter[2])
        )

    async def _take(
        self,
        session: Any,
        item: AppliedLimit,
        counter: Counter,
        since: int,
        estimate: float,
        now: int,
    ) -> bool:
        """Add ``estimate`` to the counter, and for a block limit only if it still fits.

        Earlier buckets of a rolling window only change on settle, so summing them in the
        condition is safe; the current bucket is the row being locked and re-checked.
        """
        statement = update(SpendCounterModel).where(self._row(counter))
        if item.limit.mode == "block":
            earlier = (
                select(
                    func.coalesce(
                        func.sum(SpendCounterModel.spent_usd + SpendCounterModel.reserved_usd), 0.0
                    )
                )
                .where(
                    SpendCounterModel.limit_id == counter[0],
                    SpendCounterModel.subject_key == counter[1],
                    SpendCounterModel.window_start >= since,
                    SpendCounterModel.window_start < counter[2],
                )
                .scalar_subquery()
            )
            statement = statement.where(
                SpendCounterModel.spent_usd + SpendCounterModel.reserved_usd + estimate + earlier
                <= item.amount_usd
            )
        result = await session.execute(
            statement.values(
                reserved_usd=SpendCounterModel.reserved_usd + estimate, updated_at=now
            ).returning(SpendCounterModel.limit_id)
        )
        return result.first() is not None

    @staticmethod
    async def _used(session: Any, counter: Counter, since: int) -> float:
        value = await session.scalar(
            select(
                func.coalesce(
                    func.sum(SpendCounterModel.spent_usd + SpendCounterModel.reserved_usd), 0.0
                )
            ).where(
                SpendCounterModel.limit_id == counter[0],
                SpendCounterModel.subject_key == counter[1],
                SpendCounterModel.window_start >= since,
                SpendCounterModel.window_start <= counter[2],
            )
        )
        return float(value or 0.0)

    async def _remember(self, reservation: Reservation, now: int) -> None:
        if self._db is None:
            self._memory.reservations[reservation.id] = (reservation, now)
            return
        async with self._db.session(tenant_org_id=reservation.org_id) as session:
            session.add(
                SpendReservationModel(
                    id=reservation.id,
                    org_id=reservation.org_id,
                    amount_usd=reservation.amount_usd,
                    counters=[list(c) for c in reservation.counters],
                    created_at=now,
                )
            )

    async def settle(self, reservation: Reservation, *, actual_usd: float) -> None:
        actual = max(0.0, float(actual_usd))
        if self._db is None:
            async with self._memory.lock:
                if self._memory.reservations.pop(reservation.id, None) is None:
                    return
                for counter in reservation.counters:
                    values = self._memory.values.setdefault(counter, [0.0, 0.0])
                    values[1] = max(0.0, values[1] - reservation.amount_usd)
                    values[0] += actual
            return
        async with self._db.session(tenant_org_id=reservation.org_id) as session:
            removed = await session.execute(
                delete(SpendReservationModel)
                .where(SpendReservationModel.id == reservation.id)
                .returning(SpendReservationModel.id)
            )
            if removed.first() is None:
                return  # already released by the sweeper; its estimate was given back there
            await self._release(session, reservation.counters, reservation.amount_usd, actual)

    async def _release(
        self, session: Any, counters: tuple[Counter, ...], estimate: float, actual: float
    ) -> None:
        now = int(time.time())
        for counter in counters:
            await session.execute(
                update(SpendCounterModel)
                .where(self._row(counter))
                .values(
                    reserved_usd=func.greatest(SpendCounterModel.reserved_usd - estimate, 0.0),
                    spent_usd=SpendCounterModel.spent_usd + actual,
                    updated_at=now,
                )
            )

    async def release_stale(self, admin_db: Any = None, *, older_than: int | None = None) -> int:
        """Give back reservations whose call never settled (the process died mid-call).

        Crosses tenants, so it runs on the schema-owner connection (``admin_db``), like the
        other sweeps; without a database it sweeps the in-process reservations.
        """
        cutoff = int(time.time()) - (
            older_than if older_than is not None else STALE_RESERVATION_SECONDS
        )
        if self._db is None:
            async with self._memory.lock:
                stale = [r for r, at in self._memory.reservations.values() if at < cutoff]
                for reservation in stale:
                    self._memory.reservations.pop(reservation.id, None)
                    for counter in reservation.counters:
                        values = self._memory.values.setdefault(counter, [0.0, 0.0])
                        values[1] = max(0.0, values[1] - reservation.amount_usd)
            return len(stale)
        manager: Any = admin_db if admin_db is not None else self._db
        async with manager.session() as session:
            rows = (
                await session.execute(
                    delete(SpendReservationModel)
                    .where(SpendReservationModel.created_at < cutoff)
                    .returning(SpendReservationModel.counters, SpendReservationModel.amount_usd)
                )
            ).all()
            for counters, amount in rows:
                await self._release(
                    session, tuple((str(a), str(b), int(c)) for a, b, c in counters), amount, 0.0
                )
        return len(rows)

    # -- status ------------------------------------------------------------------------

    async def status(self, scope: ModelScope) -> BudgetStatus:
        """Every limit that applies to this caller's next call, with what is used."""
        applied = await self.applied(scope)
        now = time.time()
        found: list[LimitStatus] = []
        for item in applied:
            span = window(item.limit.period, now)
            counter = (item.limit.id, item.subject_key, span.current)
            if self._db is None:
                used = self._memory.used(*counter[:2], span.since, span.current)
            else:
                async with self._db.session(tenant_org_id=scope.org_id) as session:
                    used = await self._used(session, counter, span.since)
            found.append(LimitStatus(item.limit, item.amount_usd, used, span.resets_at))
        return BudgetStatus(found)
