"""PostgreSQL-backed SessionStore implementation conforming to SessionStoreProtocol."""

import time
from typing import Any

from sqlalchemy import select

from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.session import (
    SessionRecord,
    SessionSource,
    SessionStoreProtocol,
)

from .models import SessionModel
from .session import DatabaseSessionManager


class PostgresSessionStore(SessionStoreProtocol):
    """PostgreSQL session repository implementing tenant-isolated persistence."""

    def __init__(self, manager: DatabaseSessionManager) -> None:
        self.manager = manager

    def _chat_message_to_dict(self, msg: ChatMessage) -> dict[str, Any]:
        data: dict[str, Any] = {
            "role": msg.role,
            "content": msg.content,
        }
        if msg.tool_call_id is not None:
            data["tool_call_id"] = msg.tool_call_id
        if msg.tool_calls is not None:
            data["tool_calls"] = msg.tool_calls
        return data

    def _dict_to_chat_message(self, data: dict[str, Any]) -> ChatMessage:
        return ChatMessage(
            role=data.get("role", "user"),
            content=data.get("content", ""),
            tool_calls=data.get("tool_calls"),
            tool_call_id=data.get("tool_call_id"),
        )

    def _model_to_record(self, model: SessionModel) -> SessionRecord:
        history = [self._dict_to_chat_message(d) for d in (model.conversation_history or [])]
        try:
            status = SandboxStatus(model.sandbox_status)
        except ValueError:
            status = SandboxStatus.NON_EXISTENT

        try:
            source = SessionSource(model.source)
        except ValueError:
            source = SessionSource.WEB_UI

        meta = model.metadata_json or {}
        return SessionRecord(
            session_id=model.id,
            tenant_org_id=model.org_id,
            tenant_user_id=model.user_id,
            title=model.title,
            source=source,
            created_at=model.created_at,
            updated_at=model.updated_at,
            sandbox_status=status,
            container_image=model.container_image,
            git_repo=model.git_repo,
            git_branch=model.git_branch,
            conversation_history=history,
            metadata=meta,
            is_shared=bool(meta.get("is_shared", False)),
            collaborators=meta.get("collaborators", []),
            checklist=meta.get("checklist", []),
        )

    async def create_session(self, record: SessionRecord) -> None:
        """Persist a new session record into PostgreSQL under the tenant RLS context."""
        async with self.manager.session(tenant_org_id=record.tenant_org_id) as session:
            history_data = [self._chat_message_to_dict(m) for m in record.conversation_history]
            model = SessionModel(
                id=record.session_id,
                org_id=record.tenant_org_id,
                user_id=record.tenant_user_id,
                title=record.title,
                source=record.source.value,
                sandbox_status=record.sandbox_status.value,
                container_image=record.container_image,
                git_repo=record.git_repo,
                git_branch=record.git_branch,
                conversation_history=history_data,
                metadata_json=record.metadata,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            session.add(model)

    async def get_session(
        self, session_id: str, tenant_org_id: str | None = None
    ) -> SessionRecord | None:
        """Retrieve a session by its unique ID, filtered by Row-Level Security."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            stmt = select(SessionModel).where(SessionModel.id == session_id)
            if tenant_org_id and session.bind and session.bind.dialect.name != "postgresql":
                # For SQLite / non-postgres dialects, manually filter by org_id
                stmt = stmt.where(SessionModel.org_id == tenant_org_id)

            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return None
            return self._model_to_record(model)

    async def update_session(self, record: SessionRecord) -> None:
        """Update session conversation history and status."""
        async with self.manager.session(tenant_org_id=record.tenant_org_id) as session:
            stmt = select(SessionModel).where(SessionModel.id == record.session_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model:
                model.title = record.title
                model.sandbox_status = record.sandbox_status.value
                model.container_image = record.container_image
                model.git_repo = record.git_repo
                model.git_branch = record.git_branch
                model.conversation_history = [
                    self._chat_message_to_dict(m) for m in record.conversation_history
                ]
                model.metadata_json = record.metadata
                model.updated_at = int(time.time())

    async def list_sessions(
        self,
        tenant_org_id: str,
        tenant_user_id: str | None = None,
        limit: int = 50,
    ) -> list[SessionRecord]:
        """List sessions filtered by tenant organization and optional user ID."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            stmt = select(SessionModel).where(SessionModel.org_id == tenant_org_id)
            if tenant_user_id:
                stmt = stmt.where(SessionModel.user_id == tenant_user_id)
            stmt = stmt.order_by(SessionModel.updated_at.desc()).limit(limit)

            result = await session.execute(stmt)
            models = result.scalars().all()
            return [self._model_to_record(m) for m in models]

    async def delete_session(self, session_id: str, tenant_org_id: str | None = None) -> bool:
        """Delete a session by its unique ID."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            stmt = select(SessionModel).where(SessionModel.id == session_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return False
            await session.delete(model)
            return True

    async def clear_all(self, tenant_org_id: str | None = None) -> None:
        """Purge all sessions from the database store."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            stmt = select(SessionModel)
            if tenant_org_id and session.bind and session.bind.dialect.name != "postgresql":
                stmt = stmt.where(SessionModel.org_id == tenant_org_id)
            result = await session.execute(stmt)
            models = result.scalars().all()
            for m in models:
                await session.delete(m)
