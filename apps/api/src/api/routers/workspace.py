"""Workspace browser API: list, read and edit files in a session's sandbox.

Everything is confined to ``/workspace`` and limited in size; reading is allowed to anyone who
can see the session, writing needs write access. Edits are made as the person, not the agent,
so they do not go through the agent's approval gate.
"""

from __future__ import annotations

import posixpath

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sandbox_driver.blobs import BlobTooLargeError, read_bytes
from sandbox_driver.exceptions import SandboxError

from api.auth.access import _check_session_access, _get_request_identity
from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus
from specifications.interfaces.session import SessionStoreProtocol

router = APIRouter(prefix="/v1", tags=["Workspace"])

WORKSPACE_ROOT = "/workspace"
# Kubernetes reads a file as base64 through one exec whose output is clamped at 1M characters,
# so the largest text file that survives that round trip intact is a little over 700 KB.
MAX_FILE_BYTES = 512 * 1024
MAX_TREE_ENTRIES = 2000
_HIDDEN_ENTRIES = frozenset({".git"})


class TreeEntry(BaseModel):
    name: str
    path: str = Field(..., description="Absolute path inside the workspace")
    is_dir: bool
    size_bytes: int


class TreeResponse(BaseModel):
    path: str
    entries: list[TreeEntry]
    truncated: bool = False


class FileContent(BaseModel):
    path: str
    content: str
    size_bytes: int


class WriteFileRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=1000)
    content: str


def confine(path: str) -> str:
    """The absolute path inside ``/workspace`` that ``path`` names, else a 400."""
    raw = (path or ".").strip()
    candidate = posixpath.normpath(
        raw if raw.startswith("/") else posixpath.join(WORKSPACE_ROOT, raw)
    )
    if candidate != WORKSPACE_ROOT and not candidate.startswith(WORKSPACE_ROOT + "/"):
        raise HTTPException(status_code=400, detail="Path must stay inside the workspace")
    return candidate


async def _driver_for(
    session_id: str, request: Request, *, require_write: bool
) -> SandboxDriverProtocol:
    _, user_id, roles = _get_request_identity(request)
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, user_id, roles, require_write=require_write)
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)
    if driver is None or await driver.get_status(session_id) != SandboxStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="The sandbox is not running; send a message to start it, then try again.",
        )
    return driver


@router.get("/sessions/{session_id}/workspace/tree", response_model=TreeResponse)
async def list_directory(
    session_id: str, request: Request, path: str = Query(default=".")
) -> TreeResponse:
    driver = await _driver_for(session_id, request, require_write=False)
    directory = confine(path)
    try:
        stats = await driver.list_files(session_id, directory, max_depth=1)
    except SandboxError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    entries = [
        TreeEntry(
            name=posixpath.basename(s.path),
            path=s.path,
            is_dir=s.is_dir,
            size_bytes=s.size_bytes,
        )
        for s in stats
        if posixpath.dirname(s.path) == directory
        and posixpath.basename(s.path) not in _HIDDEN_ENTRIES
    ]
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return TreeResponse(
        path=directory,
        entries=entries[:MAX_TREE_ENTRIES],
        truncated=len(entries) > MAX_TREE_ENTRIES,
    )


@router.get("/sessions/{session_id}/workspace/file", response_model=FileContent)
async def read_workspace_file(
    session_id: str, request: Request, path: str = Query(..., min_length=1)
) -> FileContent:
    driver = await _driver_for(session_id, request, require_write=False)
    target = confine(path)
    try:
        # The entry's own stat comes from its parent's listing: that is the same for a file and
        # a directory, which a listing of the path itself is not.
        siblings = await driver.list_files(session_id, posixpath.dirname(target), max_depth=1)
        stat = next((s for s in siblings if s.path == target), None)
        if stat is None:
            raise HTTPException(status_code=404, detail="File not found")
        if stat.is_dir:
            raise HTTPException(status_code=400, detail="That is a directory")
        if stat.size_bytes > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="File is larger than 512 KiB")
        content = await driver.read_file(session_id, target)
    except SandboxError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    if "\x00" in content:
        raise HTTPException(status_code=415, detail="Binary files cannot be shown")
    return FileContent(path=target, content=content, size_bytes=stat.size_bytes)


_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


@router.get("/sessions/{session_id}/workspace/raw")
async def read_workspace_image(
    session_id: str, request: Request, path: str = Query(..., min_length=1)
) -> Response:
    """An image from the workspace (screenshots, assets) for the browser's preview.

    Images only, served with a ``sandbox`` content-security-policy: an SVG is a document that
    can carry script, and this is content the agent (or the repository) produced.
    """
    driver = await _driver_for(session_id, request, require_write=False)
    target = confine(path)
    media_type = _IMAGE_TYPES.get(posixpath.splitext(target)[1].lower())
    if media_type is None:
        raise HTTPException(status_code=415, detail="Only images can be served raw")
    try:
        data = await read_bytes(driver, session_id, target, max_bytes=MAX_IMAGE_BYTES)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail="File not found") from err
    except BlobTooLargeError as err:
        raise HTTPException(status_code=413, detail="Image is larger than 5 MiB") from err
    except SandboxError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    return Response(
        content=data,
        media_type=media_type,
        headers={"content-security-policy": "sandbox", "x-content-type-options": "nosniff"},
    )


@router.put("/sessions/{session_id}/workspace/file")
async def write_workspace_file(
    session_id: str, body: WriteFileRequest, request: Request
) -> dict[str, str]:
    driver = await _driver_for(session_id, request, require_write=True)
    target = confine(body.path)
    if target == WORKSPACE_ROOT:
        raise HTTPException(status_code=400, detail="That is the workspace directory")
    if len(body.content.encode("utf-8")) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 512 KiB")
    try:
        await driver.write_file(session_id, target, body.content, overwrite=True)
    except SandboxError as err:
        raise HTTPException(status_code=500, detail=str(err)) from err
    return {"status": "saved", "path": target}
