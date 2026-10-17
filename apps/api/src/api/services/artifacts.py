"""Durable persistence for session artifacts (deliverables outside sandbox)."""

from __future__ import annotations

import logging
import time

from sqlalchemy import select

from api.db.models import SessionArtifactModel
from api.db.session import DatabaseSessionManager
from specifications.interfaces.session import ArtifactStoreProtocol, SessionArtifact

logger = logging.getLogger(__name__)


class InMemoryArtifactStore(ArtifactStoreProtocol):
    """In-memory artifact storage for test harnesses and local runs."""

    def __init__(self) -> None:
        self._artifacts: dict[tuple[str, str, str], SessionArtifact] = {}

    async def create_artifact(self, artifact: SessionArtifact) -> SessionArtifact:
        key = (artifact.tenant_org_id, artifact.session_id, artifact.id)
        self._artifacts[key] = artifact
        return artifact

    async def get_artifact(
        self, session_id: str, artifact_id: str, tenant_org_id: str
    ) -> SessionArtifact | None:
        return self._artifacts.get((tenant_org_id, session_id, artifact_id))

    async def list_artifacts(self, session_id: str, tenant_org_id: str) -> list[SessionArtifact]:
        return [
            art
            for (org, sess, _), art in self._artifacts.items()
            if org == tenant_org_id and sess == session_id
        ]

    async def update_artifact(
        self, session_id: str, artifact_id: str, tenant_org_id: str, content: str
    ) -> SessionArtifact | None:
        art = await self.get_artifact(session_id, artifact_id, tenant_org_id)
        if art is None:
            return None
        art.content = content
        art.version += 1
        art.updated_at = int(time.time())
        return art


class PostgresArtifactStore(ArtifactStoreProtocol):
    """PostgreSQL storage with forced Row-Level Security for session artifacts."""

    def __init__(self, db_mgr: DatabaseSessionManager) -> None:
        self.db_mgr = db_mgr

    async def create_artifact(self, artifact: SessionArtifact) -> SessionArtifact:
        async with self.db_mgr.session(tenant_org_id=artifact.tenant_org_id) as session:
            model = SessionArtifactModel(
                id=artifact.id,
                session_id=artifact.session_id,
                org_id=artifact.tenant_org_id,
                title=artifact.title,
                filename=artifact.filename,
                artifact_type=artifact.artifact_type,
                version=artifact.version,
                content=artifact.content,
                metadata_json=artifact.metadata,
                created_at=artifact.created_at,
                updated_at=artifact.updated_at,
            )
            session.add(model)
            await session.commit()
            return artifact

    async def get_artifact(
        self, session_id: str, artifact_id: str, tenant_org_id: str
    ) -> SessionArtifact | None:
        async with self.db_mgr.session(tenant_org_id=tenant_org_id) as session:
            stmt = select(SessionArtifactModel).where(
                SessionArtifactModel.session_id == session_id,
                SessionArtifactModel.id == artifact_id,
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if row is None:
                return None
            return self._to_artifact(row)

    async def list_artifacts(self, session_id: str, tenant_org_id: str) -> list[SessionArtifact]:
        async with self.db_mgr.session(tenant_org_id=tenant_org_id) as session:
            stmt = (
                select(SessionArtifactModel)
                .where(SessionArtifactModel.session_id == session_id)
                .order_by(SessionArtifactModel.created_at.asc())
            )
            res = await session.execute(stmt)
            return [self._to_artifact(row) for row in res.scalars()]

    async def update_artifact(
        self, session_id: str, artifact_id: str, tenant_org_id: str, content: str
    ) -> SessionArtifact | None:
        async with self.db_mgr.session(tenant_org_id=tenant_org_id) as session:
            stmt = select(SessionArtifactModel).where(
                SessionArtifactModel.session_id == session_id,
                SessionArtifactModel.id == artifact_id,
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if row is None:
                return None
            row.content = content
            row.version += 1
            row.updated_at = int(time.time())
            await session.commit()
            return self._to_artifact(row)

    def _to_artifact(self, model: SessionArtifactModel) -> SessionArtifact:
        return SessionArtifact(
            id=model.id,
            session_id=model.session_id,
            tenant_org_id=model.org_id,
            title=model.title,
            filename=model.filename,
            artifact_type=model.artifact_type,
            version=model.version,
            content=model.content,
            metadata=dict(model.metadata_json) if model.metadata_json else {},
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


_in_memory_artifact_store = InMemoryArtifactStore()


def get_artifact_store(
    db_mgr: DatabaseSessionManager | None,
) -> ArtifactStoreProtocol:
    if db_mgr is not None:
        return PostgresArtifactStore(db_mgr)
    return _in_memory_artifact_store
