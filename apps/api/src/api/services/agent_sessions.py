"""An agent's other sessions: which ones it answered in, and bounded, audited reads of them.

``session_agents`` records each (session, agent) pair when a turn runs. An agent whose
``reads_agent_sessions`` switch is on gets ``BoundAgentSessions`` in its turn: it lists the
sessions of its organization it answered in and reads a window of one. Direct messages and
private threads are never listed or read, whoever asks. A read returns the person's and the
agent's words only (no tool calls or results), each message and the whole window capped, and
is written to the audit log.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db.models import SessionAgentModel, SessionModel
from api.db.session import DatabaseSessionManager
from api.services.audit import AuditService
from specifications.interfaces.agent import (
    AgentSessionExcerpt,
    AgentSessionsProtocol,
    AgentSessionSummary,
)
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol

logger = logging.getLogger(__name__)

MAX_LISTED_SESSIONS = 30
MAX_WINDOW_MESSAGES = 50
DEFAULT_WINDOW_MESSAGES = 20
MAX_MESSAGE_CHARS = 2_000
MAX_EXCERPT_CHARS = 16_000
MAX_TITLE_CHARS = 120
# Listing reads a few more rows than asked: private sessions are dropped after the query.
LIST_OVERSAMPLE = 3
PRIVATE_MARKERS = ("slack_is_dm", "slack_private", "slack_private_thread")


def is_private(metadata: Mapping[str, Any] | None) -> bool:
    """A direct message or a private thread: never read by another session's agent."""
    return any(bool((metadata or {}).get(key)) for key in PRIVATE_MARKERS)


def _place(source: str, metadata: Mapping[str, Any] | None) -> str:
    channel = str((metadata or {}).get("slack_channel_id") or "")
    if channel:
        return f"slack:{channel}"
    return source


class InMemorySessionAgents:
    """Participation kept in the process (tests and runs without a database)."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], tuple[str, int, int]] = {}

    async def record(self, org_id: str, session_id: str, agent_id: str, now: int) -> None:
        first = self._rows.get((session_id, agent_id), (org_id, now, now))[1]
        self._rows[(session_id, agent_id)] = (org_id, first, now)

    async def answered(self, org_id: str, session_id: str, agent_id: str) -> bool:
        row = self._rows.get((session_id, agent_id))
        return row is not None and row[0] == org_id

    async def summaries(
        self,
        org_id: str,
        agent_id: str,
        exclude: str,
        query: str,
        limit: int,
        sessions: SessionStoreProtocol,
    ) -> list[AgentSessionSummary]:
        ids = sorted(
            (
                (row[2], session_id)
                for (session_id, agent), row in self._rows.items()
                if agent == agent_id and row[0] == org_id and session_id != exclude
            ),
            reverse=True,
        )
        found: list[AgentSessionSummary] = []
        for _, session_id in ids:
            record = await sessions.get_session(session_id)
            if record is None or record.tenant_org_id != org_id or is_private(record.metadata):
                continue
            if query and query.lower() not in (record.title or "").lower():
                continue
            found.append(_summary_of(record))
            if len(found) >= limit:
                break
        return found


class PostgresSessionAgents:
    """``session_agents`` joined to ``sessions`` under the tenant's row-level security."""

    def __init__(self, db: DatabaseSessionManager) -> None:
        self._db = db

    async def record(self, org_id: str, session_id: str, agent_id: str, now: int) -> None:
        statement = pg_insert(SessionAgentModel).values(
            session_id=session_id, agent_id=agent_id, org_id=org_id, first_at=now, last_at=now
        )
        async with self._db.session(tenant_org_id=org_id) as db:
            await db.execute(
                statement.on_conflict_do_update(
                    index_elements=["session_id", "agent_id"], set_={"last_at": now}
                )
            )

    async def answered(self, org_id: str, session_id: str, agent_id: str) -> bool:
        async with self._db.session(tenant_org_id=org_id) as db:
            row = await db.get(SessionAgentModel, (session_id, agent_id))
            return row is not None

    async def summaries(
        self,
        org_id: str,
        agent_id: str,
        exclude: str,
        query: str,
        limit: int,
        sessions: SessionStoreProtocol,
    ) -> list[AgentSessionSummary]:
        statement = (
            select(
                SessionModel.id,
                SessionModel.title,
                SessionModel.source,
                SessionAgentModel.last_at,
                SessionModel.metadata_json,
                func.jsonb_array_length(SessionModel.conversation_history),
            )
            .join(SessionModel, SessionModel.id == SessionAgentModel.session_id)
            .where(SessionAgentModel.agent_id == agent_id, SessionAgentModel.session_id != exclude)
            .order_by(SessionAgentModel.last_at.desc())
            .limit(limit * LIST_OVERSAMPLE)
        )
        if query:
            statement = statement.where(SessionModel.title.ilike(f"%{query}%"))
        async with self._db.session(tenant_org_id=org_id) as db:
            rows = (await db.execute(statement)).all()
        found = [
            AgentSessionSummary(
                session_id=row[0],
                title=(row[1] or "")[:MAX_TITLE_CHARS],
                source=str(row[2]),
                updated_at=int(row[3]),
                messages=int(row[5] or 0),
                place=_place(str(row[2]), row[4]),
            )
            for row in rows
            if not is_private(row[4])
        ]
        return found[:limit]


def _summary_of(record: SessionRecord) -> AgentSessionSummary:
    source = getattr(record.source, "value", str(record.source))
    return AgentSessionSummary(
        session_id=record.session_id,
        title=(record.title or "")[:MAX_TITLE_CHARS],
        source=source,
        updated_at=record.updated_at,
        messages=len(record.conversation_history),
        place=_place(source, record.metadata),
    )


SessionAgents = InMemorySessionAgents | PostgresSessionAgents
_in_memory_participation = InMemorySessionAgents()


def session_agents_store(db_mgr: DatabaseSessionManager | None) -> SessionAgents:
    if db_mgr is not None:
        return PostgresSessionAgents(db_mgr)
    return _in_memory_participation


async def record_participation(
    db_mgr: DatabaseSessionManager | None, org_id: str, session_id: str, agent_id: str
) -> None:
    """Note that ``agent_id`` answered in ``session_id``; never fails a turn."""
    if not agent_id:
        return
    try:
        await session_agents_store(db_mgr).record(org_id, session_id, agent_id, int(time.time()))
    except Exception as err:
        logger.warning("Could not record that %s answered in %s: %s", agent_id, session_id, err)


def render_excerpt(record: SessionRecord, first_index: int, limit: int) -> AgentSessionExcerpt:
    """The window's spoken messages, each and all capped, saying what was cut."""
    spoken = [
        m for m in record.conversation_history if m.role in ("user", "assistant") and m.content
    ]
    start = max(0, first_index)
    window = spoken[start : start + max(1, min(limit, MAX_WINDOW_MESSAGES))]
    parts: list[str] = []
    used, truncated, shown = 0, False, 0
    for offset, message in enumerate(window):
        text = message.content or ""
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[:MAX_MESSAGE_CHARS] + f" … [{len(text) - MAX_MESSAGE_CHARS:,} chars cut]"
            truncated = True
        speaker = "Person" if message.role == "user" else "Agent"
        part = f"[{start + offset}] {speaker}: {text}"
        if used + len(part) > MAX_EXCERPT_CHARS:
            parts.append(
                f"… window cut at {MAX_EXCERPT_CHARS:,} characters; read on from "
                f"first_index={start + offset}"
            )
            truncated = True
            break
        parts.append(part)
        used += len(part)
        shown += 1
    return AgentSessionExcerpt(
        session_id=record.session_id,
        title=(record.title or "")[:MAX_TITLE_CHARS],
        total_messages=len(spoken),
        first_index=start,
        text="\n\n".join(parts),
        truncated=truncated,
        shown=shown,
    )


@dataclass
class BoundAgentSessions(AgentSessionsProtocol):
    """The turn agent's sessions, for one turn: its organization, agent and current session."""

    participation: SessionAgents
    sessions: SessionStoreProtocol
    audit: AuditService
    org_id: str
    agent_id: str
    current_session_id: str
    actor_user_id: str | None = None

    async def list_sessions(self, query: str, limit: int) -> list[AgentSessionSummary]:
        bounded = max(1, min(limit, MAX_LISTED_SESSIONS))
        return await self.participation.summaries(
            self.org_id,
            self.agent_id,
            self.current_session_id,
            (query or "").strip(),
            bounded,
            self.sessions,
        )

    async def read_session(
        self, session_id: str, first_index: int, limit: int
    ) -> AgentSessionExcerpt | None:
        if session_id == self.current_session_id:
            return None
        if not await self.participation.answered(self.org_id, session_id, self.agent_id):
            return None
        record = await self.sessions.get_session(session_id)
        if record is None or record.tenant_org_id != self.org_id or is_private(record.metadata):
            return None
        excerpt = render_excerpt(record, first_index, limit or DEFAULT_WINDOW_MESSAGES)
        await self.audit.record(
            "agent_session.read",
            org_id=self.org_id,
            actor_user_id=self.actor_user_id,
            session_id=self.current_session_id,
            tool_name="agent_session_read",
            result_summary=f"read {session_id} from message {excerpt.first_index}",
            metadata={"agent_id": self.agent_id, "read_session_id": session_id},
        )
        return excerpt
