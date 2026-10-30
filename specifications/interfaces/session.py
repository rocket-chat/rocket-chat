"""
Interface definitions for Session State & Persistence Models.
Defines the structure of sessions stored in PostgreSQL.
"""

import base64
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .llm import ChatMessage
from .sandbox import SandboxStatus


class SessionSource(str, Enum):
    WEB_UI = "web_ui"
    SLACK = "slack"
    GITHUB_WEBHOOK = "github_webhook"
    CLI = "cli"
    SCHEDULED = "scheduled"


@dataclass
class SessionRecord:
    session_id: str
    tenant_org_id: str
    tenant_user_id: str
    title: str
    source: SessionSource = SessionSource.WEB_UI
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))
    sandbox_status: SandboxStatus = SandboxStatus.NON_EXISTENT
    container_image: str | None = None
    git_repo: str | None = None
    git_branch: str | None = None
    conversation_history: list[ChatMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    is_shared: bool = False
    collaborators: list[str] = field(default_factory=list)
    checklist: list[dict[str, Any]] = field(default_factory=list)
    # Sidebar ergonomics: pinning floats a session to the top of the
    # sidebar, archiving hides it from the default listing.
    is_pinned: bool = False
    is_archived: bool = False
    is_processing: bool = False


# The reasoning efforts a person may choose for a session (metadata key ``reasoning_effort``) or
# for one turn; "none" switches reasoning off. A turn's own choice wins over the session's.
SESSION_REASONING_EFFORTS: tuple[str, ...] = ("none", "minimal", "low", "medium", "high")
SESSION_REASONING_EFFORT_KEY = "reasoning_effort"


def session_reasoning_effort(metadata: dict[str, Any] | None) -> str | None:
    """The reasoning effort saved on a session, or None when it follows the agent and organization."""
    value = (metadata or {}).get(SESSION_REASONING_EFFORT_KEY)
    return value if isinstance(value, str) and value in SESSION_REASONING_EFFORTS else None


@dataclass(frozen=True)
class SessionCursor:
    """The keyset position of a session in ``(updated_at, session_id)`` order.

    Both fields are carried because ``updated_at`` alone is not unique — many sessions
    can share a second — so pagination must fall through to the immutable id to stay
    skip-free and duplicate-free across pages.
    """

    updated_at: int
    session_id: str


@dataclass
class SessionPage:
    """One keyset page of sessions plus the cursor that continues after it."""

    records: list[SessionRecord] = field(default_factory=list)
    next_cursor: str | None = None


def encode_session_cursor(cursor: SessionCursor) -> str:
    """Encode a keyset position as a URL-safe, opaque cursor token."""
    raw = f"{cursor.updated_at}:{cursor.session_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_session_cursor(token: str) -> SessionCursor:
    """Decode a cursor token, raising ``ValueError`` when it is malformed.

    A malformed cursor is a client error, never a silent fallback to page one: silently
    resetting would make a paging loop repeat the first page forever.
    """
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + padding).decode("utf-8")
        updated_at_text, _, session_id = raw.partition(":")
        return SessionCursor(updated_at=int(updated_at_text), session_id=session_id)
    except (ValueError, UnicodeDecodeError) as err:
        raise ValueError(f"Invalid session cursor: {token!r}") from err


class SessionStoreProtocol(ABC):
    """Contract for persisting and retrieving session states from PostgreSQL."""

    @abstractmethod
    async def create_session(self, record: SessionRecord) -> None:
        """Persists a new session record."""
        pass

    @abstractmethod
    async def get_session(
        self,
        session_id: str,
        tenant_org_id: str | None = None,
        *,
        include_history: bool = True,
    ) -> SessionRecord | None:
        """Retrieves a session record by its unique ID.

        ``tenant_org_id`` scopes the read to one organization where the store enforces tenancy
        (row-level security); a store that keeps one process-wide dict ignores it.
        ``include_history=False`` leaves ``conversation_history`` empty: a read of who may see
        the session or where it stands need not load a transcript that can run to megabytes.
        """
        pass

    @abstractmethod
    async def update_session(self, record: SessionRecord) -> None:
        """Updates session conversation history and metadata."""
        pass

    async def mutate_session(
        self,
        session_id: str,
        tenant_org_id: str,
        mutate: Callable[[SessionRecord], None],
    ) -> SessionRecord | None:
        """Apply ``mutate`` to the stored record and save it, atomically where the store can.

        Read-modify-write through ``get_session`` + ``update_session`` loses updates when two
        writers overlap (a turn appending its answer while the title task renames the
        session: the later write restores the earlier read). Stores that can serialise per
        session override this (PostgreSQL does, under a row lock); ``mutate`` must therefore
        be synchronous and quick. Returns the saved record, or ``None`` if there is none.
        """
        record = await self.get_session(session_id)
        if record is None:
            return None
        mutate(record)
        await self.update_session(record)
        return record

    @abstractmethod
    async def list_sessions(
        self,
        tenant_org_id: str,
        tenant_user_id: str | None = None,
        limit: int = 50,
    ) -> list[SessionRecord]:
        """Lists active and past sessions for a tenant."""
        pass

    @abstractmethod
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
        """Lists one keyset page of sessions, newest ``(updated_at, session_id)`` first.

        ``visible_to_user_id`` applies the non-admin access rule (owner, shared, or
        collaborator); ``tenant_user_id`` is an exact owner filter an admin may request.
        ``query`` is a case-insensitive substring match on the title.
        """
        pass

    @abstractmethod
    async def set_session_flags(
        self,
        session_id: str,
        tenant_org_id: str,
        *,
        is_pinned: bool | None = None,
        is_archived: bool | None = None,
    ) -> SessionRecord | None:
        """Update the pin/archive flags of one session, returning the updated record."""
        pass

    @abstractmethod
    async def delete_session(self, session_id: str, tenant_org_id: str | None = None) -> bool:
        """Deletes a session record by its unique ID (scoped like ``get_session``)."""
        pass


class GoalStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


@dataclass
class SessionGoal:
    """Overarching goal and safety budgets for autonomous multi-turn continuations."""

    id: str
    session_id: str
    tenant_org_id: str
    description: str
    status: GoalStatus = GoalStatus.ACTIVE
    max_wall_time_seconds: int | None = None
    max_total_tokens: int | None = None
    max_cost_usd: float | None = None
    consumed_tokens: int = 0
    consumed_cost_usd: float = 0.0
    continuation_count: int = 0
    max_continuations: int = 10
    stop_condition: str | None = None
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))
    completed_at: int | None = None


class GoalStoreProtocol(ABC):
    """Storage protocol for autonomous session goals and safety budgets."""

    @abstractmethod
    async def create_goal(self, goal: SessionGoal) -> None:
        """Persist a new session goal."""
        pass

    @abstractmethod
    async def get_goal(self, session_id: str, tenant_org_id: str) -> SessionGoal | None:
        """Get the active goal for a session, if any."""
        pass

    @abstractmethod
    async def update_goal(self, goal: SessionGoal) -> None:
        """Update an existing session goal."""
        pass

    @abstractmethod
    async def record_consumption(
        self,
        session_id: str,
        tenant_org_id: str,
        tokens: int,
        cost_usd: float,
    ) -> tuple[SessionGoal | None, bool, str | None]:
        """Record token and cost consumption; returns (goal, budget_exceeded, warning_reason)."""
        pass


@dataclass
class SessionArtifact:
    """Durable session deliverable stored independently of the sandbox lifecycle."""

    id: str
    session_id: str
    tenant_org_id: str
    title: str
    filename: str
    artifact_type: str = "markdown"  # 'markdown', 'diff', 'diagram', 'html', 'json'
    version: int = 1
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))


@dataclass
class ArtifactVersion:
    """One saved version of an artifact (``content`` is only filled by ``get_version``)."""

    version: int
    created_at: int
    size: int = 0
    content: str | None = None


class ArtifactStoreProtocol(ABC):
    """Storage protocol for durable session artifacts."""

    @abstractmethod
    async def create_artifact(self, artifact: SessionArtifact) -> SessionArtifact:
        """Create and persist a new session artifact."""
        pass

    @abstractmethod
    async def get_artifact(
        self, session_id: str, artifact_id: str, tenant_org_id: str
    ) -> SessionArtifact | None:
        """Retrieve an artifact by its ID."""
        pass

    @abstractmethod
    async def list_artifacts(self, session_id: str, tenant_org_id: str) -> list[SessionArtifact]:
        """List all artifacts for a session."""
        pass

    @abstractmethod
    async def update_artifact(
        self, session_id: str, artifact_id: str, tenant_org_id: str, content: str
    ) -> SessionArtifact | None:
        """Update artifact content, incrementing version and updated_at."""
        pass

    @abstractmethod
    async def list_versions(
        self, session_id: str, artifact_id: str, tenant_org_id: str
    ) -> list[ArtifactVersion]:
        """The saved versions of an artifact, oldest first, without their content."""
        pass

    @abstractmethod
    async def get_version(
        self, session_id: str, artifact_id: str, tenant_org_id: str, version: int
    ) -> ArtifactVersion | None:
        """One saved version of an artifact with its content."""
        pass

    @abstractmethod
    async def delete_artifacts(self, session_id: str, tenant_org_id: str) -> None:
        """Delete all artifacts for a session (and their versions)."""
        pass
