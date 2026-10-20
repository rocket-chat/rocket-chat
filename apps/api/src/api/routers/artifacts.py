"""Session artifacts REST API."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from api.auth.access import _check_session_access, _get_request_identity
from api.services.artifacts import get_artifact_store
from specifications.interfaces.session import SessionArtifact, SessionStoreProtocol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Artifacts"])


class ArtifactSummaryResponse(BaseModel):
    """Metadata summary of a session artifact."""

    id: str = Field(description="Unique artifact ID")
    session_id: str = Field(description="Session this artifact belongs to")
    title: str = Field(description="Human-readable title")
    filename: str = Field(description="Filename with extension")
    artifact_type: str = Field(description="Artifact type (markdown, diff, diagram, html, json)")
    version: int = Field(description="Version increment")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: int = Field(description="Creation epoch timestamp")
    updated_at: int = Field(description="Last update epoch timestamp")


class ArtifactDetailResponse(ArtifactSummaryResponse):
    """Detailed artifact with full content."""

    content: str = Field(description="Full text/code content of the artifact")


class ArtifactVersionResponse(BaseModel):
    """One saved version of an artifact (its content is read with ``?version=``)."""

    version: int = Field(description="Version number, from 1")
    created_at: int = Field(description="When this version was saved (epoch seconds)")
    size: int = Field(description="Characters in this version")


def _to_summary(art: SessionArtifact) -> ArtifactSummaryResponse:
    return ArtifactSummaryResponse(
        id=art.id,
        session_id=art.session_id,
        title=art.title,
        filename=art.filename,
        artifact_type=art.artifact_type,
        version=art.version,
        metadata=art.metadata,
        created_at=art.created_at,
        updated_at=art.updated_at,
    )


def _to_detail(art: SessionArtifact) -> ArtifactDetailResponse:
    return ArtifactDetailResponse(
        id=art.id,
        session_id=art.session_id,
        title=art.title,
        filename=art.filename,
        artifact_type=art.artifact_type,
        version=art.version,
        metadata=art.metadata,
        created_at=art.created_at,
        updated_at=art.updated_at,
        content=art.content,
    )


@router.get(
    "/sessions/{session_id}/artifacts",
    response_model=list[ArtifactSummaryResponse],
    summary="List all durable artifacts for a session",
)
async def list_session_artifacts(
    session_id: str, request: Request
) -> list[ArtifactSummaryResponse]:
    """Retrieve all artifacts created in this session."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _, user_id, roles = _get_request_identity(request)
    _check_session_access(record, user_id, roles)

    db_mgr = getattr(request.app.state, "db_manager", None)
    artifact_store = get_artifact_store(db_mgr)
    rows = await artifact_store.list_artifacts(session_id, record.tenant_org_id)
    return [_to_summary(r) for r in rows]


@router.get(
    "/sessions/{session_id}/artifacts/{artifact_id}",
    response_model=ArtifactDetailResponse,
    summary="Get detailed artifact content and metadata",
)
async def get_session_artifact(
    session_id: str,
    artifact_id: str,
    request: Request,
    version: int | None = Query(
        default=None, ge=1, description="An earlier version; omit for the current one"
    ),
) -> ArtifactDetailResponse:
    """Retrieve a single artifact including its complete content (or one of its versions)."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _, user_id, roles = _get_request_identity(request)
    _check_session_access(record, user_id, roles)

    db_mgr = getattr(request.app.state, "db_manager", None)
    artifact_store = get_artifact_store(db_mgr)
    art = await artifact_store.get_artifact(session_id, artifact_id, record.tenant_org_id)
    if art is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if version is not None and version != art.version:
        saved = await artifact_store.get_version(
            session_id, artifact_id, record.tenant_org_id, version
        )
        if saved is None or saved.content is None:
            raise HTTPException(status_code=404, detail="Version not found")
        art = replace(
            art, version=saved.version, content=saved.content, updated_at=saved.created_at
        )
    return _to_detail(art)


@router.get(
    "/sessions/{session_id}/artifacts/{artifact_id}/versions",
    response_model=list[ArtifactVersionResponse],
    summary="List the saved versions of an artifact",
)
async def list_artifact_versions(
    session_id: str, artifact_id: str, request: Request
) -> list[ArtifactVersionResponse]:
    """Every version the artifact has had, oldest first."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _, user_id, roles = _get_request_identity(request)
    _check_session_access(record, user_id, roles)

    artifact_store = get_artifact_store(getattr(request.app.state, "db_manager", None))
    if await artifact_store.get_artifact(session_id, artifact_id, record.tenant_org_id) is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    versions = await artifact_store.list_versions(session_id, artifact_id, record.tenant_org_id)
    return [
        ArtifactVersionResponse(version=v.version, created_at=v.created_at, size=v.size)
        for v in versions
    ]


@router.get(
    "/sessions/{session_id}/artifacts/{artifact_id}/raw",
    summary="Download or stream raw artifact content",
)
async def get_session_artifact_raw(session_id: str, artifact_id: str, request: Request) -> Response:
    """Stream raw content with appropriate Content-Type header."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _, user_id, roles = _get_request_identity(request)
    _check_session_access(record, user_id, roles)

    db_mgr = getattr(request.app.state, "db_manager", None)
    artifact_store = get_artifact_store(db_mgr)
    art = await artifact_store.get_artifact(session_id, artifact_id, record.tenant_org_id)
    if art is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    media_type = "text/plain"
    if art.artifact_type == "markdown":
        media_type = "text/markdown; charset=utf-8"
    elif art.artifact_type == "json":
        media_type = "application/json"
    elif art.artifact_type == "html":
        media_type = "text/html; charset=utf-8"

    return Response(content=art.content, media_type=media_type)
