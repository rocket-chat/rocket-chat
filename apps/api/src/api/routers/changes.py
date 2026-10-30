"""The Changes view: what the session changed in its repositories, and undoing one file.

Changes are read with git in the running sandbox against a base (see
``services/workspace_changes``), so they are right after a reload, a restart or in a second tab.
Undoing a file is an explicit act by someone with write access to the session, like a checkpoint
rewind, so it does not go through the agent's approval gate.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.auth.access import _check_session_access, _get_request_identity
from api.services import workspace_changes as changes
from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus
from specifications.interfaces.session import SessionStoreProtocol

router = APIRouter(prefix="/v1", tags=["Changes"])

ScopeParam = Literal["session", "turn"]


class ChangedFileResponse(BaseModel):
    path: str = Field(..., description="Path inside the repository")
    status: Literal["added", "modified", "deleted"]
    additions: int = Field(..., description="Lines added")
    deletions: int = Field(..., description="Lines removed")
    binary: bool = Field(default=False, description="Binary files have no line counts or diff")


class RepositoryChangesResponse(BaseModel):
    path: str = Field(..., description="Repository folder in the sandbox, e.g. /workspace/api")
    name: str
    base_label: str = Field(..., description="What the files are compared with, in words")
    files: list[ChangedFileResponse]
    truncated: bool = Field(default=False, description="More files changed than are listed")


class ChangesResponse(BaseModel):
    available: bool = Field(
        ..., description="False when the sandbox is not running, so nothing can be read"
    )
    scope: ScopeParam
    repositories: list[RepositoryChangesResponse] = Field(default_factory=list)


class FileVersionsResponse(BaseModel):
    path: str
    original: str | None = Field(default=None, description="At the base; absent for a new file")
    modified: str | None = Field(default=None, description="Now; absent for a deleted file")
    binary: bool = False
    truncated: bool = Field(default=False, description="The file was cut at the size limit")


class RevertRequest(BaseModel):
    repository: str = Field(..., min_length=1, max_length=300)
    path: str = Field(..., min_length=1, max_length=1000)
    scope: ScopeParam = "session"


async def _workspace(
    session_id: str, request: Request, *, require_write: bool
) -> SandboxDriverProtocol | None:
    """The session's running sandbox, or None when there is nothing to read."""
    _, user_id, roles = _get_request_identity(request)
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, user_id, roles, require_write=require_write)
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)
    if driver is None or await driver.get_status(session_id) != SandboxStatus.RUNNING:
        return None
    return driver


@router.get("/sessions/{session_id}/changes", response_model=ChangesResponse)
async def list_changes(
    session_id: str, request: Request, scope: ScopeParam = "session"
) -> ChangesResponse:
    """Files the session changed in each repository, against where the work started.

    ``scope=session`` compares with the point the work branched from the default branch;
    ``scope=turn`` with the snapshot taken before the latest turn first changed the workspace.
    """
    driver = await _workspace(session_id, request, require_write=False)
    if driver is None:
        return ChangesResponse(available=False, scope=scope)
    found = await changes.session_changes(driver, session_id, scope)
    return ChangesResponse(
        available=True,
        scope=scope,
        repositories=[
            RepositoryChangesResponse(
                path=repo.path,
                name=repo.name,
                base_label=repo.base_label,
                truncated=repo.truncated,
                files=[
                    ChangedFileResponse(
                        path=f.path,
                        status=f.status,
                        additions=f.additions,
                        deletions=f.deletions,
                        binary=f.binary,
                    )
                    for f in repo.files
                ],
            )
            for repo in found
        ],
    )


@router.get("/sessions/{session_id}/changes/file", response_model=FileVersionsResponse)
async def read_changed_file(
    session_id: str,
    request: Request,
    repository: str = Query(..., description="Repository folder from the changes listing"),
    path: str = Query(..., description="File path inside the repository"),
    scope: ScopeParam = "session",
) -> FileVersionsResponse:
    """A changed file at the base and as it is now, whole, for a side-by-side view."""
    driver = await _workspace(session_id, request, require_write=False)
    if driver is None:
        raise HTTPException(status_code=409, detail="The sandbox is not running")
    try:
        versions = await changes.file_versions(driver, session_id, repository, scope, path)
    except changes.ChangesError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return FileVersionsResponse(
        path=versions.path,
        original=versions.original,
        modified=versions.modified,
        binary=versions.binary,
        truncated=versions.truncated,
    )


@router.post("/sessions/{session_id}/changes/revert")
async def revert_changed_file(
    session_id: str, body: RevertRequest, request: Request
) -> dict[str, str]:
    """Put one file back as it was at the base (or delete it when it did not exist then)."""
    driver = await _workspace(session_id, request, require_write=True)
    if driver is None:
        raise HTTPException(status_code=409, detail="The sandbox is not running")
    try:
        await changes.revert_file(driver, session_id, body.repository, body.scope, body.path)
    except changes.ChangesError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return {"status": "reverted", "path": body.path}


class HunkView(BaseModel):
    index: int
    header: str = Field(..., description="The hunk's @@ line")
    added: int
    removed: int
    preview: str = Field(..., description="Its first lines")


class RevertHunkRequest(RevertRequest):
    index: int = Field(..., ge=0, description="Which hunk of the file, from the hunk listing")


@router.get("/sessions/{session_id}/changes/hunks", response_model=list[HunkView])
async def list_file_hunks(
    session_id: str,
    request: Request,
    repository: str = Query(..., min_length=1, max_length=300),
    path: str = Query(..., min_length=1, max_length=1000),
    scope: ScopeParam = "session",
) -> list[HunkView]:
    """The changed regions of one file, to undo them one at a time."""
    driver = await _workspace(session_id, request, require_write=False)
    if driver is None:
        raise HTTPException(status_code=409, detail="The sandbox is not running")
    try:
        found = await changes.file_hunks(driver, session_id, repository, scope, path)
    except changes.ChangesError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return [HunkView(**asdict(h)) for h in found]


@router.post("/sessions/{session_id}/changes/revert-hunk")
async def revert_changed_hunk(
    session_id: str, body: RevertHunkRequest, request: Request
) -> dict[str, str]:
    """Undo one changed region of a file; the rest of the file is kept."""
    driver = await _workspace(session_id, request, require_write=True)
    if driver is None:
        raise HTTPException(status_code=409, detail="The sandbox is not running")
    try:
        await changes.revert_hunk(
            driver, session_id, body.repository, body.scope, body.path, body.index
        )
    except changes.ChangesError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return {"status": "reverted", "path": body.path}
