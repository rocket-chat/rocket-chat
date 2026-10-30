"""Durable persistence for subagent execution transcripts (Item D2)."""

from __future__ import annotations

import logging

from sqlalchemy import select

from api.db.models import SubagentTranscriptModel
from api.db.session import DatabaseSessionManager
from specifications.interfaces.agent import (
    SubagentTranscriptRecord,
    SubagentTranscriptStoreProtocol,
)

logger = logging.getLogger(__name__)


class InMemorySubagentTranscriptStore(SubagentTranscriptStoreProtocol):
    """In-memory subagent transcript storage for test harnesses and offline runs."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], SubagentTranscriptRecord] = {}

    async def save_transcript(self, record: SubagentTranscriptRecord) -> None:
        key = (record.org_id, record.session_id, record.subagent_id)
        self._records[key] = record

    async def get_transcript(
        self, session_id: str, subagent_id: str, org_id: str
    ) -> SubagentTranscriptRecord | None:
        return self._records.get((org_id, session_id, subagent_id))

    async def list_transcripts(
        self, session_id: str, org_id: str
    ) -> list[SubagentTranscriptRecord]:
        return [
            r
            for (r_org, r_sess, _), r in self._records.items()
            if r_org == org_id and r_sess == session_id
        ]


class PostgresSubagentTranscriptStore(SubagentTranscriptStoreProtocol):
    """PostgreSQL storage with forced Row-Level Security for subagent transcripts."""

    def __init__(self, db_mgr: DatabaseSessionManager) -> None:
        self.db_mgr = db_mgr

    async def save_transcript(self, record: SubagentTranscriptRecord) -> None:
        async with self.db_mgr.session(tenant_org_id=record.org_id) as session:
            model = SubagentTranscriptModel(
                id=record.id,
                session_id=record.session_id,
                org_id=record.org_id,
                subagent_id=record.subagent_id,
                role=record.role,
                task=record.task,
                summary=record.summary,
                status=record.status,
                turns_taken=record.turns_taken,
                tool_calls_count=record.tool_calls_count,
                duration_ms=record.duration_ms,
                tokens=record.tokens,
                cost_usd=record.cost_usd,
                changed_files=record.changed_files,
                artefacts=record.artefacts,
                messages=record.messages,
                created_at=record.created_at,
                report=record.report,
                model=record.model,
            )
            session.add(model)
            await session.commit()

    async def get_transcript(
        self, session_id: str, subagent_id: str, org_id: str
    ) -> SubagentTranscriptRecord | None:
        async with self.db_mgr.session(tenant_org_id=org_id) as session:
            stmt = select(SubagentTranscriptModel).where(
                SubagentTranscriptModel.session_id == session_id,
                SubagentTranscriptModel.subagent_id == subagent_id,
            )
            res = (await session.scalars(stmt)).first()
            if res is None:
                return None
            return _model_to_record(res)

    async def list_transcripts(
        self, session_id: str, org_id: str
    ) -> list[SubagentTranscriptRecord]:
        async with self.db_mgr.session(tenant_org_id=org_id) as session:
            stmt = (
                select(SubagentTranscriptModel)
                .where(SubagentTranscriptModel.session_id == session_id)
                .order_by(SubagentTranscriptModel.created_at.asc())
            )
            rows = (await session.scalars(stmt)).all()
            return [_model_to_record(r) for r in rows]


def _model_to_record(m: SubagentTranscriptModel) -> SubagentTranscriptRecord:
    return SubagentTranscriptRecord(
        id=m.id,
        session_id=m.session_id,
        org_id=m.org_id,
        subagent_id=m.subagent_id,
        role=m.role,
        task=m.task,
        summary=m.summary,
        status=m.status,
        turns_taken=m.turns_taken,
        tool_calls_count=m.tool_calls_count,
        duration_ms=m.duration_ms,
        tokens=m.tokens,
        cost_usd=m.cost_usd,
        changed_files=list(m.changed_files or []),
        artefacts=list(m.artefacts or []),
        messages=list(m.messages or []),
        created_at=m.created_at,
        report=dict(m.report) if m.report else None,
        model=m.model or "",
    )


_in_memory_store = InMemorySubagentTranscriptStore()


def get_subagent_transcript_store(
    db_mgr: DatabaseSessionManager | None,
) -> SubagentTranscriptStoreProtocol:
    if db_mgr is not None:
        return PostgresSubagentTranscriptStore(db_mgr)
    return _in_memory_store
