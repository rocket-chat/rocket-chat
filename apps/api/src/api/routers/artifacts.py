"""Session artifacts REST API."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
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
    session_id: str, artifact_id: str, request: Request
) -> ArtifactDetailResponse:
    """Retrieve a single artifact including its complete content."""
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
    return _to_detail(art)


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
