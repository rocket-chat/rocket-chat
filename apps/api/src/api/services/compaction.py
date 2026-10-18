"""Durable compaction checkpoints storage."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import delete, desc, select

from api.db.models import SessionCompactionModel
from specifications.interfaces.compaction import CompactionCheckpoint, CompactionStoreProtocol
from specifications.interfaces.llm import ChatMessage


def _message_to_dict(msg: ChatMessage) -> dict[str, Any]:
    return {
        "role": msg.role,
        "content": msg.content,
        "tool_calls": msg.tool_calls,
        "tool_call_id": msg.tool_call_id,
        "reasoning": msg.reasoning,
        "reasoning_blocks": msg.reasoning_blocks,
    }


def _dict_to_message(data: dict[str, Any]) -> ChatMessage:
    return ChatMessage(
        role=data.get("role", "user"),
        content=data.get("content"),
        tool_calls=data.get("tool_calls"),
        tool_call_id=data.get("tool_call_id"),
        reasoning=data.get("reasoning"),
        reasoning_blocks=data.get("reasoning_blocks"),
    )


def _to_checkpoint(row: SessionCompactionModel) -> CompactionCheckpoint:
    return CompactionCheckpoint(
        id=row.id,
        session_id=row.session_id,
        org_id=row.org_id,
        boundary_seq=row.boundary_seq,
        summary=row.summary,
        compacted_messages=[_dict_to_message(m) for m in (row.compacted_messages or [])],
        tokens_before=row.tokens_before,
        tokens_after=row.tokens_after,
        created_at=row.created_at,
    )


class InMemoryCompactionStore(CompactionStoreProtocol):
    """In-memory store for compaction checkpoints during tests and development."""

    def __init__(self) -> None:
        self._checkpoints: dict[tuple[str, str], list[CompactionCheckpoint]] = {}

    async def record_compaction(self, checkpoint: CompactionCheckpoint) -> None:
        key = (checkpoint.org_id, checkpoint.session_id)
        records = self._checkpoints.setdefault(key, [])
        for idx, rec in enumerate(records):
            if rec.boundary_seq == checkpoint.boundary_seq:
                records[idx] = checkpoint
                return
        records.append(checkpoint)

    async def get_latest_compaction(
        self, session_id: str, org_id: str
    ) -> CompactionCheckpoint | None:
        records = self._checkpoints.get((org_id, session_id), [])
        return records[-1] if records else None

    async def list_compactions(self, session_id: str, org_id: str) -> list[CompactionCheckpoint]:
        return list(self._checkpoints.get((org_id, session_id), []))

    async def delete_compactions(self, session_id: str, org_id: str) -> None:
        self._checkpoints.pop((org_id, session_id), None)


class PostgresCompactionStore(CompactionStoreProtocol):
    """PostgreSQL RLS-scoped storage for compaction checkpoints."""

    def __init__(self, db_manager: Any) -> None:
        self._db = db_manager

    async def record_compaction(self, checkpoint: CompactionCheckpoint) -> None:
        now = int(time.time())
        compacted_dict = [_message_to_dict(m) for m in checkpoint.compacted_messages]
        checkpoint_id = checkpoint.id or f"cmp_{uuid.uuid4().hex[:24]}"
        created_at = checkpoint.created_at or now

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(SessionCompactionModel)
            .values(
                id=checkpoint_id,
                session_id=checkpoint.session_id,
                org_id=checkpoint.org_id,
                boundary_seq=checkpoint.boundary_seq,
                summary=checkpoint.summary,
                compacted_messages=compacted_dict,
                tokens_before=checkpoint.tokens_before,
                tokens_after=checkpoint.tokens_after,
                created_at=created_at,
            )
            .on_conflict_do_update(
                constraint="uq_session_compactions_boundary",
                set_={
                    "summary": checkpoint.summary,
                    "compacted_messages": compacted_dict,
                    "tokens_before": checkpoint.tokens_before,
                    "tokens_after": checkpoint.tokens_after,
                },
            )
        )
        async with self._db.session(tenant_org_id=checkpoint.org_id) as session:
            await session.execute(stmt)

    async def get_latest_compaction(
        self, session_id: str, org_id: str
    ) -> CompactionCheckpoint | None:
        async with self._db.session(tenant_org_id=org_id) as session:
            stmt = (
                select(SessionCompactionModel)
                .where(SessionCompactionModel.session_id == session_id)
                .order_by(desc(SessionCompactionModel.boundary_seq))
                .limit(1)
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _to_checkpoint(row) if row is not None else None

    async def list_compactions(self, session_id: str, org_id: str) -> list[CompactionCheckpoint]:
        async with self._db.session(tenant_org_id=org_id) as session:
            stmt = (
                select(SessionCompactionModel)
                .where(SessionCompactionModel.session_id == session_id)
                .order_by(SessionCompactionModel.boundary_seq.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_to_checkpoint(r) for r in rows]

    async def delete_compactions(self, session_id: str, org_id: str) -> None:
        async with self._db.session(tenant_org_id=org_id) as session:
            await session.execute(
                delete(SessionCompactionModel).where(
                    SessionCompactionModel.session_id == session_id
                )
            )
