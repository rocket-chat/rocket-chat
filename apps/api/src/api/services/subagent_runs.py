"""Background subagents as supervised tasks (``subagent_runs``).

The replica running a background child holds a lease on its run and renews it with the turn
lease heartbeat. A child cannot be carried over to another replica (it lives inside its
orchestrator's task, with the turn's collaborators), so when its replica stops the run is
settled as ``failed`` by whichever replica notices the expired lease, and the agent is told it
can delegate the task again.
"""

from __future__ import annotations

import dataclasses
import logging
import time

from sqlalchemy import and_, select, update

from api.db.models import SubagentRunModel
from api.db.session import DatabaseSessionManager
from api.services.turn_trace import PROCESS_INSTANCE_ID
from specifications.interfaces.agent import (
    SUBAGENT_LEASE_SECONDS,
    SUBAGENT_RUN_ACTIVE,
    SubagentRunRecord,
    SubagentRunStoreProtocol,
)

logger = logging.getLogger(__name__)

LOST_REPLICA_SUMMARY = (
    "The replica running this subagent stopped before it finished; its work in progress is "
    "lost. Delegate the task again if it is still needed."
)


def _leased(record: SubagentRunRecord, now: int) -> SubagentRunRecord:
    """An active run is leased to this process; a finished one holds no lease."""
    if record.status in SUBAGENT_RUN_ACTIVE:
        return dataclasses.replace(
            record, lease_owner=PROCESS_INSTANCE_ID, lease_expires_at=now + SUBAGENT_LEASE_SECONDS
        )
    return dataclasses.replace(record, lease_owner="", lease_expires_at=0)


def _lost(record: SubagentRunRecord, now: int) -> SubagentRunRecord:
    result = dict(record.result or {})
    result.update(
        role=record.role,
        task=record.task,
        summary=LOST_REPLICA_SUMMARY,
        status="failed",
        subagent_id=record.subagent_id,
        background=True,
    )
    return dataclasses.replace(
        record, status="failed", result=result, lease_owner="", lease_expires_at=0, updated_at=now
    )


class InMemorySubagentRunStore(SubagentRunStoreProtocol):
    """Runs held in memory (tests and deployments without a database)."""

    def __init__(self) -> None:
        self._runs: dict[tuple[str, str, str], SubagentRunRecord] = {}

    async def save_run(self, record: SubagentRunRecord) -> None:
        key = (record.org_id, record.session_id, record.subagent_id)
        self._runs[key] = _leased(record, int(time.time()))

    async def get_run(
        self, org_id: str, session_id: str, subagent_id: str
    ) -> SubagentRunRecord | None:
        return self._runs.get((org_id, session_id, subagent_id))

    async def list_runs(self, org_id: str, session_id: str) -> list[SubagentRunRecord]:
        return sorted(
            (r for (o, s, _), r in self._runs.items() if o == org_id and s == session_id),
            key=lambda r: r.created_at,
        )

    async def renew_leases(self, owner: str, until: int) -> int:
        renewed = 0
        for key, run in self._runs.items():
            if run.lease_owner == owner and run.status in SUBAGENT_RUN_ACTIVE:
                self._runs[key] = dataclasses.replace(run, lease_expires_at=until)
                renewed += 1
        return renewed

    async def claim_expired(self, owner: str, now: int, until: int) -> list[SubagentRunRecord]:
        lost: list[SubagentRunRecord] = []
        for key, run in self._runs.items():
            if run.status in SUBAGENT_RUN_ACTIVE and run.lease_expires_at < now:
                self._runs[key] = _lost(run, now)
                lost.append(self._runs[key])
        return lost


def _to_record(row: SubagentRunModel) -> SubagentRunRecord:
    return SubagentRunRecord(
        id=row.id,
        org_id=row.org_id,
        session_id=row.session_id,
        subagent_id=row.subagent_id,
        role=row.role,
        task=row.task,
        context=row.context,
        status=row.status,
        background=row.background,
        parent_tool_call_id=row.parent_tool_call_id,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        attempts=row.attempts,
        result=dict(row.result) if row.result else None,
        cost_usd=row.cost_usd,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


_COLUMNS = tuple(f.name for f in dataclasses.fields(SubagentRunRecord))


class PostgresSubagentRunStore(SubagentRunStoreProtocol):
    """Runs in ``subagent_runs``; tenant reads under RLS, recovery on the administrative
    connection (it settles every organization's lost runs)."""

    def __init__(self, db_mgr: DatabaseSessionManager) -> None:
        self.db_mgr = db_mgr

    async def save_run(self, record: SubagentRunRecord) -> None:
        leased = _leased(record, int(time.time()))
        async with self.db_mgr.session(tenant_org_id=record.org_id) as session:
            row = await session.get(SubagentRunModel, record.id)
            if row is None:
                session.add(SubagentRunModel(**{c: getattr(leased, c) for c in _COLUMNS}))
                return
            for column in _COLUMNS:
                setattr(row, column, getattr(leased, column))

    async def get_run(
        self, org_id: str, session_id: str, subagent_id: str
    ) -> SubagentRunRecord | None:
        async with self.db_mgr.session(tenant_org_id=org_id) as session:
            row = (
                await session.scalars(
                    select(SubagentRunModel).where(
                        SubagentRunModel.org_id == org_id,
                        SubagentRunModel.session_id == session_id,
                        SubagentRunModel.subagent_id == subagent_id,
                    )
                )
            ).first()
            return _to_record(row) if row is not None else None

    async def list_runs(self, org_id: str, session_id: str) -> list[SubagentRunRecord]:
        async with self.db_mgr.session(tenant_org_id=org_id) as session:
            rows = await session.scalars(
                select(SubagentRunModel)
                .where(SubagentRunModel.org_id == org_id, SubagentRunModel.session_id == session_id)
                .order_by(SubagentRunModel.created_at.asc())
            )
            return [_to_record(r) for r in rows.all()]

    async def renew_leases(self, owner: str, until: int) -> int:
        async with self.db_mgr.session() as session:
            result = await session.execute(
                update(SubagentRunModel)
                .where(
                    SubagentRunModel.lease_owner == owner,
                    SubagentRunModel.status.in_(SUBAGENT_RUN_ACTIVE),
                )
                .values(lease_expires_at=until)
            )
            return int(getattr(result, "rowcount", 0) or 0)

    async def claim_expired(self, owner: str, now: int, until: int) -> list[SubagentRunRecord]:
        async with self.db_mgr.session() as session:
            rows = (
                await session.scalars(
                    select(SubagentRunModel)
                    .where(
                        and_(
                            SubagentRunModel.status.in_(SUBAGENT_RUN_ACTIVE),
                            SubagentRunModel.lease_expires_at < now,
                        )
                    )
                    # Two replicas sweeping at once settle each run once.
                    .with_for_update(skip_locked=True)
                )
            ).all()
            lost = []
            for row in rows:
                settled = _lost(_to_record(row), now)
                row.status, row.result = settled.status, settled.result
                row.lease_owner, row.lease_expires_at = "", 0
                row.updated_at = now
                lost.append(settled)
            return lost


_memory_store = InMemorySubagentRunStore()


def get_subagent_run_store(db_mgr: DatabaseSessionManager | None) -> SubagentRunStoreProtocol:
    return PostgresSubagentRunStore(db_mgr) if db_mgr is not None else _memory_store


async def supervise_subagent_runs(admin_manager: DatabaseSessionManager | None) -> int:
    """Renew this replica's leases, and settle the runs whose replica went silent."""
    store = get_subagent_run_store(admin_manager)
    now = int(time.time())
    await store.renew_leases(PROCESS_INSTANCE_ID, now + SUBAGENT_LEASE_SECONDS)
    lost = await store.claim_expired(PROCESS_INSTANCE_ID, now, now + SUBAGENT_LEASE_SECONDS)
    if lost:
        logger.warning("Settled %d background subagent run(s) whose replica stopped", len(lost))
    return len(lost)
