"""In-memory session repository implementing SessionStoreProtocol."""

import time

from specifications.interfaces.session import (
    SessionRecord,
    SessionStoreProtocol,
)


class InMemorySessionStore(SessionStoreProtocol):
    """In-memory repository managing session persistence and state lookup."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}

    async def create_session(self, record: SessionRecord) -> None:
        """Persist a new session record."""
        self._sessions[record.session_id] = record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        """Retrieve a session by its unique ID."""
        return self._sessions.get(session_id)

    async def update_session(self, record: SessionRecord) -> None:
        """Update session record with latest timestamp and conversation history."""
        record.updated_at = int(time.time())
        self._sessions[record.session_id] = record

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
        return matching[:limit]

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session by its unique ID."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    async def clear_all(self) -> None:
        """Purge all sessions from the in-memory store."""
        self._sessions.clear()
