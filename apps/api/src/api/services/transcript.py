"""Transcript stores: the durable LLM message log."""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from typing import Any

from agent_core.transcript import slice_user_turns
from sqlalchemy import delete, func, select

from api.db.models import SessionMessageModel
from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.transcript import TranscriptStoreProtocol


def _to_row(
    session_id: str, org_id: str, seq: int, message: ChatMessage, now: int
) -> SessionMessageModel:
    return SessionMessageModel(
        id=f"msg_{uuid.uuid4().hex[:24]}",
        session_id=session_id,
        org_id=org_id,
        seq=seq,
        role=message.role,
        content=message.content,
        tool_calls=message.tool_calls,
        tool_call_id=message.tool_call_id,
        reasoning=message.reasoning,
        created_at=now,
    )


def _from_row(row: SessionMessageModel) -> ChatMessage:
    return ChatMessage(
        role=row.role,
        content=row.content,
        tool_calls=row.tool_calls,
        tool_call_id=row.tool_call_id,
        reasoning=row.reasoning,
    )


def _clone(message: ChatMessage) -> ChatMessage:
    # Attachments and cache markers are request-local; they are never part of the log.
    return ChatMessage(
        role=message.role,
        content=message.content,
        tool_calls=message.tool_calls,
        tool_call_id=message.tool_call_id,
        reasoning=message.reasoning,
    )


class InMemoryTranscriptStore(TranscriptStoreProtocol):
    """Process-local log for deployments without a database and for tests."""

    def __init__(self) -> None:
        self._logs: dict[tuple[str, str], list[ChatMessage]] = {}

    async def load(
        self, session_id: str, org_id: str, since_seq: int | None = None
    ) -> list[ChatMessage]:
        messages = self._logs.get((org_id, session_id), [])
        if since_seq is not None:
            messages = messages[since_seq + 1 :]
        return [_clone(m) for m in messages]

    async def latest_seq(self, session_id: str, org_id: str) -> int | None:
        messages = self._logs.get((org_id, session_id), [])
        return len(messages) - 1 if messages else None

    async def append(self, session_id: str, org_id: str, messages: Sequence[ChatMessage]) -> None:
        self._logs.setdefault((org_id, session_id), []).extend(_clone(m) for m in messages)

    async def copy_prefix(
        self,
        source_session_id: str,
        target_session_id: str,
        org_id: str,
        user_turns: int | None = None,
    ) -> int:
        prefix = slice_user_turns(self._logs.get((org_id, source_session_id), []), user_turns)
        self._logs[(org_id, target_session_id)] = [_clone(m) for m in prefix]
        return len(prefix)

    async def truncate_to_turn(self, session_id: str, org_id: str, user_turns: int) -> int:
        messages = self._logs.get((org_id, session_id), [])
        prefix = slice_user_turns(messages, user_turns)
        self._logs[(org_id, session_id)] = [_clone(m) for m in prefix]
        return len(prefix)

    async def delete(self, session_id: str, org_id: str) -> None:
        self._logs.pop((org_id, session_id), None)


class PostgresTranscriptStore(TranscriptStoreProtocol):
    """RLS-scoped ``session_messages`` table."""

    def __init__(self, db_manager: Any) -> None:
        self._db = db_manager

    async def load(
        self, session_id: str, org_id: str, since_seq: int | None = None
    ) -> list[ChatMessage]:
        async with self._db.session(tenant_org_id=org_id) as session:
            stmt = select(SessionMessageModel).where(SessionMessageModel.session_id == session_id)
            if since_seq is not None:
                stmt = stmt.where(SessionMessageModel.seq > since_seq)
            rows = (await session.execute(stmt.order_by(SessionMessageModel.seq))).scalars()
            return [_from_row(row) for row in rows]

    async def latest_seq(self, session_id: str, org_id: str) -> int | None:
        async with self._db.session(tenant_org_id=org_id) as session:
            val = (
                await session.execute(
                    select(func.max(SessionMessageModel.seq)).where(
                        SessionMessageModel.session_id == session_id
                    )
                )
            ).scalar_one_or_none()
            return int(val) if val is not None else None

    async def append(self, session_id: str, org_id: str, messages: Sequence[ChatMessage]) -> None:
        if not messages:
            return
        now = int(time.time())
        async with self._db.session(tenant_org_id=org_id) as session:
            last = (
                await session.execute(
                    select(func.max(SessionMessageModel.seq)).where(
                        SessionMessageModel.session_id == session_id
                    )
                )
            ).scalar_one_or_none()
            next_seq = 0 if last is None else int(last) + 1
            session.add_all(
                _to_row(session_id, org_id, next_seq + offset, message, now)
                for offset, message in enumerate(messages)
            )

    async def copy_prefix(
        self,
        source_session_id: str,
        target_session_id: str,
        org_id: str,
        user_turns: int | None = None,
    ) -> int:
        prefix = slice_user_turns(await self.load(source_session_id, org_id), user_turns)
        async with self._db.session(tenant_org_id=org_id) as session:
            await session.execute(
                delete(SessionMessageModel).where(
                    SessionMessageModel.session_id == target_session_id
                )
            )
        await self.append(target_session_id, org_id, prefix)
        return len(prefix)

    async def truncate_to_turn(self, session_id: str, org_id: str, user_turns: int) -> int:
        all_msgs = await self.load(session_id, org_id)
        prefix = slice_user_turns(all_msgs, user_turns)
        async with self._db.session(tenant_org_id=org_id) as session:
            await session.execute(
                delete(SessionMessageModel).where(SessionMessageModel.session_id == session_id)
            )
        await self.append(session_id, org_id, prefix)
        return len(prefix)

    async def delete(self, session_id: str, org_id: str) -> None:
        async with self._db.session(tenant_org_id=org_id) as session:
            await session.execute(
                delete(SessionMessageModel).where(SessionMessageModel.session_id == session_id)
            )
