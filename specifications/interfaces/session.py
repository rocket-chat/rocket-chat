"""
Interface definitions for Session State & Persistence Models.
Defines the structure of sessions stored in PostgreSQL.
"""

import time
from abc import ABC, abstractmethod
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


class SessionStoreProtocol(ABC):
    """Contract for persisting and retrieving session states from PostgreSQL."""

    @abstractmethod
    async def create_session(self, record: SessionRecord) -> None:
        """Persists a new session record."""
        pass

    @abstractmethod
    async def get_session(self, session_id: str) -> SessionRecord | None:
        """Retrieves a session record by its unique ID."""
        pass

    @abstractmethod
    async def update_session(self, record: SessionRecord) -> None:
        """Updates session conversation history and metadata."""
        pass

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
    async def delete_session(self, session_id: str) -> bool:
        """Deletes a session record by its unique ID."""
        pass
