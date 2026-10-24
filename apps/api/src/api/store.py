"""In-memory session repository implementing SessionStoreProtocol."""

import time
from collections.abc import Callable
from dataclasses import replace

from specifications.interfaces.session import (
    SessionCursor,
    SessionPage,
    SessionRecord,
    SessionStoreProtocol,
    encode_session_cursor,
)


def _is_visible_to(record: SessionRecord, user_id: str) -> bool:
    """Non-admin access rule: owner, explicitly shared, or a listed collaborator."""
    if record.tenant_user_id == user_id:
        return True
    meta = record.metadata if isinstance(record.metadata, dict) else {}
    if record.is_shared or bool(meta.get("is_shared", False)):
        return True
    collaborators = record.collaborators or meta.get("collaborators", [])
    return user_id in collaborators


class InMemorySessionStore(SessionStoreProtocol):
    """In-memory repository managing session persistence and state lookup."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}

    async def create_session(self, record: SessionRecord) -> None:
        """Persist a new session record."""
        self._sessions[record.session_id] = record

    async def get_session(
        self, session_id: str, tenant_org_id: str | None = None
    ) -> SessionRecord | None:
        """Retrieve a session by its unique ID (one process-wide dict: no tenant scoping)."""
        return self._sessions.get(session_id)

    async def update_session(self, record: SessionRecord) -> None:
        """Update session record with latest timestamp and conversation history."""
        record.updated_at = int(time.time())
        self._sessions[record.session_id] = record

    async def mutate_session(
        self,
        session_id: str,
        tenant_org_id: str,
        mutate: Callable[[SessionRecord], None],
    ) -> SessionRecord | None:
        """Apply ``mutate`` to the one shared record; with no awaits in between it is atomic."""
        record = self._sessions.get(session_id)
        if record is None:
            return None
        mutate(record)
        record.updated_at = int(time.time())
        return record

    async def list_sessions(
        self,
        tenant_org_id: str,
        tenant_user_id: str | None = None,
        limit: int = 50,
    ) -> list[SessionRecord]:
        """List sessions filtered by tenant organisation and optional user ID, latest active first."""
        matching = [
            s
            for s in self._sessions.values()
            if s.tenant_org_id == tenant_org_id
            and (tenant_user_id is None or s.tenant_user_id == tenant_user_id)
        ]
        matching.sort(key=lambda s: s.updated_at, reverse=True)
        return [replace(s, conversation_history=[]) for s in matching[:limit]]

    async def list_sessions_page(
        self,
        tenant_org_id: str,
        *,
        tenant_user_id: str | None = None,
        visible_to_user_id: str | None = None,
        include_archived: bool = False,
        query: str | None = None,
        limit: int = 50,
        cursor: SessionCursor | None = None,
    ) -> SessionPage:
        """List one keyset page, newest ``(updated_at, session_id)`` first.

        This mirrors the PostgreSQL query so the deterministic E2E/CI path and the
        production path paginate identically; the tuple ordering is what makes the page
        boundary stable when sessions are created between fetches.
        """
        matching = [s for s in self._sessions.values() if s.tenant_org_id == tenant_org_id]
        if tenant_user_id:
            matching = [s for s in matching if s.tenant_user_id == tenant_user_id]
        if visible_to_user_id is not None:
            matching = [s for s in matching if _is_visible_to(s, visible_to_user_id)]
        if not include_archived:
            matching = [s for s in matching if not s.is_archived]
        if query:
            needle = query.lower()
            matching = [s for s in matching if needle in s.title.lower()]
        if cursor is not None:
            matching = [
                s
                for s in matching
                if (s.updated_at, s.session_id) < (cursor.updated_at, cursor.session_id)
            ]
        matching.sort(key=lambda s: (s.updated_at, s.session_id), reverse=True)

        page = [replace(s, conversation_history=[]) for s in matching[:limit]]
        next_cursor: str | None = None
        if len(matching) > limit and page:
            last = page[-1]
            next_cursor = encode_session_cursor(
                SessionCursor(updated_at=last.updated_at, session_id=last.session_id)
            )
        return SessionPage(records=page, next_cursor=next_cursor)

    async def set_session_flags(
        self,
        session_id: str,
        tenant_org_id: str,
        *,
        is_pinned: bool | None = None,
        is_archived: bool | None = None,
    ) -> SessionRecord | None:
        """Update the pin/archive flags of one session in place."""
        record = self._sessions.get(session_id)
        if record is None or record.tenant_org_id != tenant_org_id:
            return None
        if is_pinned is not None:
            record.is_pinned = is_pinned
        if is_archived is not None:
            record.is_archived = is_archived
        record.updated_at = int(time.time())
        return record

    async def delete_session(self, session_id: str, tenant_org_id: str | None = None) -> bool:
        """Delete a session by its unique ID."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    async def clear_all(self) -> None:
        """Purge all sessions from the in-memory store."""
        self._sessions.clear()
