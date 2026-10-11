"""Attachment REST endpoints for the web cockpit.

Uploads are multipart, tenant-scoped through the session ACL *and* PostgreSQL RLS, and
return the metadata the cockpit needs to render a chip and thumbnail. Retrieval is
signed: the token issued at upload time authorises the preview fetch, so a bare
attachment id can never be used to read another tenant's bytes.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.auth.access import _check_session_access, _get_request_identity
from api.services.attachments import (
    AttachmentError,
    AttachmentService,
    record_response,
)
from specifications.interfaces.session import SessionStoreProtocol

router = APIRouter(prefix="/v1", tags=["Attachments"])


class AttachmentResponse(BaseModel):
    """One stored attachment as returned by the upload endpoint."""

    attachment_id: str = Field(..., description="Server-assigned attachment identifier")
    name: str = Field(..., description="Sanitised file name")
    mime: str = Field(..., description="Detected content type")
    size: int = Field(..., description="Stored byte size")
    sha256: str = Field(..., description="Content hash used for dedupe")
    workspace_path: str = Field(..., description="Path inside the session workspace")
    preview: str | None = Field(
        default=None, description="Data URL thumbnail for images, else null"
    )
    retrieval_token: str | None = Field(
        default=None, description="Short-lived token for signed retrieval"
    )


class AttachmentListResponse(BaseModel):
    """Attachments already stored for a session, oldest first."""

    session_id: str
    attachments: list[AttachmentResponse]


def _get_attachment_service(request: Request) -> AttachmentService:
    """Return the app-wide service, lazily constructing it when main.py did not wire one.

    Lazy construction keeps the router self-sufficient for tests and E2E (where no
    database is configured) without editing the application bootstrap.
    """
    service: AttachmentService | None = getattr(request.app.state, "attachment_service", None)
    if service is None:
        service = AttachmentService(
            getattr(request.app.state, "db_manager", None),
            getattr(request.app.state, "sandbox_driver", None),
        )
        request.app.state.attachment_service = service
    return service


async def _require_session(request: Request, session_id: str, *, require_write: bool) -> Any:
    """Authorise the caller against the session before touching attachments."""
    store: SessionStoreProtocol = request.app.state.session_store
    _, user_id, roles = _get_request_identity(request)
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, user_id, roles, require_write=require_write)
    return record


@router.post("/sessions/{session_id}/attachments", response_model=AttachmentResponse)
async def upload_session_attachment(
    session_id: str,
    request: Request,
    file: Annotated[UploadFile, File(description="File to attach to the session")],
) -> AttachmentResponse:
    """Validate and store one uploaded file for the current turn."""
    record = await _require_session(request, session_id, require_write=True)
    service = _get_attachment_service(request)
    try:
        stored = await service.store(
            session_id=session_id,
            org_id=record.tenant_org_id,
            file=file,
        )
    except AttachmentError as err:
        # Invariant 10: a rejected upload must be visible, never a silent no-op.
        raise HTTPException(status_code=422, detail=str(err)) from err

    preview = await service.preview_data_url(stored)
    token = service.issue_token(stored.id, stored.org_id)
    payload = record_response(stored, preview)
    payload["retrieval_token"] = token
    return AttachmentResponse(**payload)


@router.get(
    "/sessions/{session_id}/attachments/{attachment_id}",
    response_model=None,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_session_attachment(
    session_id: str,
    attachment_id: str,
    token: str,
    request: Request,
) -> Response:
    """Signed retrieval of an attachment's bytes for a preview or download."""
    record = await _require_session(request, session_id, require_write=False)
    service = _get_attachment_service(request)
    if not service.verify_token(token, attachment_id, record.tenant_org_id):
        raise HTTPException(status_code=403, detail="Invalid or expired attachment token")
    stored = await service.get(attachment_id, record.tenant_org_id)
    if stored is None or stored.session_id != session_id:
        raise HTTPException(status_code=404, detail="Attachment not found")
    try:
        data = await service.read_bytes(stored)
    except AttachmentError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    return Response(content=data, media_type=stored.mime)


@router.get("/sessions/{session_id}/attachments", response_model=AttachmentListResponse)
async def list_session_attachments(
    session_id: str,
    request: Request,
) -> AttachmentListResponse:
    """List the attachments already stored for a session (cockpit reload)."""
    record = await _require_session(request, session_id, require_write=False)
    service = _get_attachment_service(request)
    records = await service.list_for_session(session_id, record.tenant_org_id)
    attachments = [
        AttachmentResponse(
            **record_response(stored, await service.preview_data_url(stored)),
        )
        for stored in records
    ]
    return AttachmentListResponse(session_id=session_id, attachments=attachments)
