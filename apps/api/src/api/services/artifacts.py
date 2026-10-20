"""Durable persistence for session artifacts (deliverables outside sandbox)."""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy import delete, func, select, update

from api.db.models import SessionArtifactModel, SessionArtifactVersionModel
from api.db.session import DatabaseSessionManager
from specifications.interfaces.session import (
    ArtifactStoreProtocol,
    ArtifactVersion,
    SessionArtifact,
)

logger = logging.getLogger(__name__)

# Characters kept of one artifact's content. Longer content is cut, and the agent is told.
MAX_ARTIFACT_CONTENT_CHARS = 500_000


class InMemoryArtifactStore(ArtifactStoreProtocol):
    """In-memory artifact storage for test harnesses and local runs."""

    def __init__(self) -> None:
        self._artifacts: dict[tuple[str, str, str], SessionArtifact] = {}
        self._versions: dict[tuple[str, str, str], list[ArtifactVersion]] = {}

    async def create_artifact(self, artifact: SessionArtifact) -> SessionArtifact:
        artifact.content = artifact.content[:MAX_ARTIFACT_CONTENT_CHARS]
        key = (artifact.tenant_org_id, artifact.session_id, artifact.id)
        self._artifacts[key] = artifact
        self._versions[key] = [
            ArtifactVersion(
                artifact.version, artifact.updated_at, len(artifact.content), artifact.content
            )
        ]
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
        art.content = content[:MAX_ARTIFACT_CONTENT_CHARS]
        art.version += 1
        art.updated_at = int(time.time())
        self._versions[(tenant_org_id, session_id, artifact_id)].append(
            ArtifactVersion(art.version, art.updated_at, len(art.content), art.content)
        )
        return art

    async def list_versions(
        self, session_id: str, artifact_id: str, tenant_org_id: str
    ) -> list[ArtifactVersion]:
        saved = self._versions.get((tenant_org_id, session_id, artifact_id), [])
        return [ArtifactVersion(v.version, v.created_at, v.size) for v in saved]

    async def get_version(
        self, session_id: str, artifact_id: str, tenant_org_id: str, version: int
    ) -> ArtifactVersion | None:
        saved = self._versions.get((tenant_org_id, session_id, artifact_id), [])
        return next((v for v in saved if v.version == version), None)

    async def delete_artifacts(self, session_id: str, tenant_org_id: str) -> None:
        for key in [k for k in self._artifacts if k[0] == tenant_org_id and k[1] == session_id]:
            self._artifacts.pop(key, None)
            self._versions.pop(key, None)


class PostgresArtifactStore(ArtifactStoreProtocol):
    """PostgreSQL storage with forced Row-Level Security for session artifacts."""

    def __init__(self, db_mgr: DatabaseSessionManager) -> None:
        self.db_mgr = db_mgr

    @staticmethod
    def _version_row(
        artifact_id: str, session_id: str, org_id: str, version: int, content: str, at: int
    ) -> SessionArtifactVersionModel:
        return SessionArtifactVersionModel(
            id=f"artv_{uuid.uuid4().hex[:20]}",
            artifact_id=artifact_id,
            session_id=session_id,
            org_id=org_id,
            version=version,
            content=content,
            created_at=at,
        )

    async def create_artifact(self, artifact: SessionArtifact) -> SessionArtifact:
        capped = artifact.content[:MAX_ARTIFACT_CONTENT_CHARS]
        artifact.content = capped
        async with self.db_mgr.session(tenant_org_id=artifact.tenant_org_id) as session:
            session.add(
                SessionArtifactModel(
                    id=artifact.id,
                    session_id=artifact.session_id,
                    org_id=artifact.tenant_org_id,
                    title=artifact.title,
                    filename=artifact.filename,
                    artifact_type=artifact.artifact_type,
                    version=artifact.version,
                    content=capped,
                    metadata_json=artifact.metadata,
                    created_at=artifact.created_at,
                    updated_at=artifact.updated_at,
                )
            )
            # The artifact row first: the version's foreign key needs it.
            await session.flush()
            session.add(
                self._version_row(
                    artifact.id,
                    artifact.session_id,
                    artifact.tenant_org_id,
                    artifact.version,
                    capped,
                    artifact.updated_at,
                )
            )
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
        capped = content[:MAX_ARTIFACT_CONTENT_CHARS]
        now = int(time.time())
        async with self.db_mgr.session(tenant_org_id=tenant_org_id) as session:
            stmt = (
                update(SessionArtifactModel)
                .where(
                    SessionArtifactModel.session_id == session_id,
                    SessionArtifactModel.id == artifact_id,
                )
                .values(
                    content=capped,
                    version=SessionArtifactModel.version + 1,
                    updated_at=now,
                )
                .returning(SessionArtifactModel)
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if row is None:
                return None
            session.add(
                self._version_row(artifact_id, session_id, tenant_org_id, row.version, capped, now)
            )
            await session.commit()
            return self._to_artifact(row)

    async def list_versions(
        self, session_id: str, artifact_id: str, tenant_org_id: str
    ) -> list[ArtifactVersion]:
        async with self.db_mgr.session(tenant_org_id=tenant_org_id) as session:
            res = await session.execute(
                select(
                    SessionArtifactVersionModel.version,
                    SessionArtifactVersionModel.created_at,
                    func.length(SessionArtifactVersionModel.content),
                )
                .where(
                    SessionArtifactVersionModel.session_id == session_id,
                    SessionArtifactVersionModel.artifact_id == artifact_id,
                )
                .order_by(SessionArtifactVersionModel.version.asc())
            )
            return [ArtifactVersion(v, at, int(size)) for v, at, size in res.all()]

    async def get_version(
        self, session_id: str, artifact_id: str, tenant_org_id: str, version: int
    ) -> ArtifactVersion | None:
        async with self.db_mgr.session(tenant_org_id=tenant_org_id) as session:
            res = await session.execute(
                select(SessionArtifactVersionModel).where(
                    SessionArtifactVersionModel.session_id == session_id,
                    SessionArtifactVersionModel.artifact_id == artifact_id,
                    SessionArtifactVersionModel.version == version,
                )
            )
            row = res.scalar_one_or_none()
            if row is None:
                return None
            return ArtifactVersion(row.version, row.created_at, len(row.content), row.content)

    async def delete_artifacts(self, session_id: str, tenant_org_id: str) -> None:
        async with self.db_mgr.session(tenant_org_id=tenant_org_id) as session:
            # The versions go with their artifacts (ON DELETE CASCADE).
            await session.execute(
                delete(SessionArtifactModel).where(SessionArtifactModel.session_id == session_id)
            )
            await session.commit()

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
