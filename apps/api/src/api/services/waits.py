"""Durable persistence and atomic claims for session waits (H1).

Persists wait conditions (timers, process exits, GitHub CI/reviews) with forced
Row-Level Security so wait state and scheduled resumptions survive process restarts
and leader failovers.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select, update

from api.db.models import SessionWaitModel
from api.db.session import DatabaseSessionManager

logger = logging.getLogger(__name__)


@dataclass
class SessionWait:
    id: str
    session_id: str
    tenant_org_id: str
    kind: str  # "time", "process_exit", "github_ci", "github_pr"
    target: str
    deadline: int
    status: str = "waiting"  # "waiting", "claimed", "completed", "failed", "cancelled"
    wake_prompt: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: int = field(default_factory=lambda: int(time.time()))
    claimed_at: int | None = None
    completed_at: int | None = None


class WaitStoreProtocol(Protocol):
    async def create_wait(self, wait: SessionWait) -> SessionWait: ...
    async def get_wait(
        self, session_id: str, wait_id: str, tenant_org_id: str
    ) -> SessionWait | None: ...
    async def list_active_waits(self, session_id: str, tenant_org_id: str) -> list[SessionWait]: ...
    async def resolve_wait(
        self, session_id: str, wait_id: str, tenant_org_id: str, status: str = "completed"
    ) -> SessionWait | None: ...


class InMemoryWaitStore(WaitStoreProtocol):
    """In-memory store for unit tests and local mock runs."""

    def __init__(self) -> None:
        self._waits: dict[tuple[str, str, str], SessionWait] = {}

    async def create_wait(self, wait: SessionWait) -> SessionWait:
        key = (wait.tenant_org_id, wait.session_id, wait.id)
        self._waits[key] = wait
        return wait

    async def get_wait(
        self, session_id: str, wait_id: str, tenant_org_id: str
    ) -> SessionWait | None:
        return self._waits.get((tenant_org_id, session_id, wait_id))

    async def list_active_waits(self, session_id: str, tenant_org_id: str) -> list[SessionWait]:
        return [
            w
            for (org, sess, _), w in self._waits.items()
            if org == tenant_org_id and sess == session_id and w.status in ("waiting", "claimed")
        ]

    async def resolve_wait(
        self, session_id: str, wait_id: str, tenant_org_id: str, status: str = "completed"
    ) -> SessionWait | None:
        key = (tenant_org_id, session_id, wait_id)
        wait = self._waits.get(key)
        if wait is None:
            return None
        wait.status = status
        wait.completed_at = int(time.time())
        return wait


class PostgresWaitStore(WaitStoreProtocol):
    """PostgreSQL storage with forced Row-Level Security for durable waits."""

    def __init__(self, db_mgr: DatabaseSessionManager) -> None:
        self.db_mgr = db_mgr

    async def create_wait(self, wait: SessionWait) -> SessionWait:
        async with self.db_mgr.session(tenant_org_id=wait.tenant_org_id) as session:
            model = SessionWaitModel(
                id=wait.id,
                session_id=wait.session_id,
                org_id=wait.tenant_org_id,
                kind=wait.kind,
                target=wait.target,
                deadline=wait.deadline,
                status=wait.status,
                wake_prompt=wait.wake_prompt,
                metadata_json=wait.metadata,
                created_at=wait.created_at,
                claimed_at=wait.claimed_at,
                completed_at=wait.completed_at,
            )
            session.add(model)
            await session.commit()
            return wait

    async def get_wait(
        self, session_id: str, wait_id: str, tenant_org_id: str
    ) -> SessionWait | None:
        async with self.db_mgr.session(tenant_org_id=tenant_org_id) as session:
            stmt = select(SessionWaitModel).where(
                SessionWaitModel.session_id == session_id,
                SessionWaitModel.id == wait_id,
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            return self._to_wait(row) if row else None

    async def list_active_waits(self, session_id: str, tenant_org_id: str) -> list[SessionWait]:
        async with self.db_mgr.session(tenant_org_id=tenant_org_id) as session:
            stmt = (
                select(SessionWaitModel)
                .where(
                    SessionWaitModel.session_id == session_id,
                    SessionWaitModel.status.in_(("waiting", "claimed")),
                )
                .order_by(SessionWaitModel.created_at.asc())
            )
            res = await session.execute(stmt)
            return [self._to_wait(row) for row in res.scalars()]

    async def resolve_wait(
        self, session_id: str, wait_id: str, tenant_org_id: str, status: str = "completed"
    ) -> SessionWait | None:
        async with self.db_mgr.session(tenant_org_id=tenant_org_id) as session:
            stmt = select(SessionWaitModel).where(
                SessionWaitModel.session_id == session_id,
                SessionWaitModel.id == wait_id,
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if row is None:
                return None
            row.status = status
            row.completed_at = int(time.time())
            await session.commit()
            return self._to_wait(row)

    def _to_wait(self, row: SessionWaitModel) -> SessionWait:
        return SessionWait(
            id=row.id,
            session_id=row.session_id,
            tenant_org_id=row.org_id,
            kind=row.kind,
            target=row.target,
            deadline=row.deadline,
            status=row.status,
            wake_prompt=row.wake_prompt,
            metadata=dict(row.metadata_json) if row.metadata_json else {},
            created_at=row.created_at,
            claimed_at=row.claimed_at,
            completed_at=row.completed_at,
        )


async def settle_waits(
    store: WaitStoreProtocol,
    session_id: str,
    tenant_org_id: str,
    *,
    status: str,
    kind: str | None = None,
    target: str | None = None,
) -> int:
    """Close the session's open waits (those on ``kind``/``target`` when given).

    A wait left open after its result arrived, or after the person stopped the session, wakes
    the session again at its deadline with nothing to do but start work nobody asked for.
    """
    settled = 0
    for wait in await store.list_active_waits(session_id, tenant_org_id):
        if (kind is None or wait.kind == kind) and (target is None or wait.target == target):
            await store.resolve_wait(session_id, wait.id, tenant_org_id, status=status)
            settled += 1
    return settled


_in_memory_wait_store = InMemoryWaitStore()


def get_wait_store(db_mgr: DatabaseSessionManager | None) -> WaitStoreProtocol:
    if db_mgr is None or not getattr(db_mgr, "session_factory", None):
        return _in_memory_wait_store
    return PostgresWaitStore(db_mgr)


async def claim_due_waits(
    admin_db: DatabaseSessionManager | None,
    store: WaitStoreProtocol,
    now: float | None = None,
) -> list[SessionWait]:
    """Atomic claim for due waits across all tenants.

    Uses the schema-owner connection to find waits whose deadline has passed or
    whose status is 'waiting' and eligible for claim. A lost race gets rowcount == 0.
    """
    moment = int(now if now is not None else time.time())
    claimed: list[SessionWait] = []

    if admin_db is None or isinstance(store, InMemoryWaitStore):
        mem = getattr(store, "_waits", {})
        for w in list(mem.values()):
            if w.status == "waiting" and w.deadline <= moment:
                w.status = "claimed"
                w.claimed_at = moment
                claimed.append(w)
        return claimed

    async with admin_db.session() as session:
        due = (
            (
                await session.execute(
                    select(SessionWaitModel).where(
                        SessionWaitModel.status == "waiting",
                        SessionWaitModel.deadline <= moment,
                    )
                )
            )
            .scalars()
            .all()
        )

        for row in due:
            res = await session.execute(
                update(SessionWaitModel)
                .where(
                    SessionWaitModel.id == row.id,
                    SessionWaitModel.status == "waiting",
                )
                .values(status="claimed", claimed_at=moment)
            )
            if getattr(res, "rowcount", 0):
                claimed.append(
                    SessionWait(
                        id=row.id,
                        session_id=row.session_id,
                        tenant_org_id=row.org_id,
                        kind=row.kind,
                        target=row.target,
                        deadline=row.deadline,
                        status="claimed",
                        wake_prompt=row.wake_prompt,
                        metadata=dict(row.metadata_json) if row.metadata_json else {},
                        created_at=row.created_at,
                        claimed_at=moment,
                    )
                )
    return claimed
