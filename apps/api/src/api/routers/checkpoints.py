"""Workspace checkpoints: list, create and rewind.

The agent snapshots the workspace before a turn first changes it (and on request); this is
the person's side of that safety net. A rewind is an explicit act by someone with write
access to the session, so it does not go through the agent's approval gate.
"""

from __future__ import annotations

from agent_core.tools import git_tools
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sandbox_driver.workspace import RepositoryNotFoundError, resolve_repository_dir

from api.auth.access import _check_session_access, _get_request_identity
from api.services.session_support import active_repository_of
from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus
from specifications.interfaces.session import SessionStoreProtocol

router = APIRouter(prefix="/v1", tags=["Checkpoints"])


class CheckpointResponse(BaseModel):
    name: str = Field(..., description="Checkpoint identifier (a git tag)")
    sha: str = Field(..., description="Snapshot commit")
    created_at: int = Field(..., description="Epoch seconds")


class CheckpointList(BaseModel):
    checkpoints: list[CheckpointResponse] = Field(default_factory=list)
    available: bool = Field(
        ..., description="False when the sandbox is not running or holds no git repository"
    )
    repository: str | None = Field(
        default=None, description="The repository folder the checkpoints belong to"
    )


class CreateCheckpointRequest(BaseModel):
    label: str | None = Field(default=None, max_length=40)


class RewindRequest(BaseModel):
    reference: str = Field(..., min_length=1, max_length=120)
    mode: str = Field(
        default="files",
        pattern="^(files|hard)$",
        description="'files' restores tracked files; 'hard' also resets the worktree and index",
    )


async def _running_workspace(
    session_id: str, request: Request, *, require_write: bool
) -> tuple[SandboxDriverProtocol, str] | None:
    """The running sandbox and the session's repository folder, or None when there is neither."""
    _, user_id, roles = _get_request_identity(request)
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, user_id, roles, require_write=require_write)
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)
    if driver is None or await driver.get_status(session_id) != SandboxStatus.RUNNING:
        return None
    try:
        folder = await resolve_repository_dir(
            driver, session_id, active=active_repository_of(record) or None
        )
    except RepositoryNotFoundError:
        return None
    return driver, folder


def _require_workspace(
    workspace: tuple[SandboxDriverProtocol, str] | None,
) -> tuple[SandboxDriverProtocol, str]:
    if workspace is None:
        raise HTTPException(
            status_code=409,
            detail="There is no repository to checkpoint: the sandbox is not running or holds "
            "no git repository.",
        )
    return workspace


@router.get("/sessions/{session_id}/checkpoints", response_model=CheckpointList)
async def list_checkpoints(session_id: str, request: Request) -> CheckpointList:
    """Checkpoints of the session's workspace, newest first."""
    workspace = await _running_workspace(session_id, request, require_write=False)
    if workspace is None:
        return CheckpointList(available=False)
    driver, folder = workspace
    found = await git_tools.list_checkpoints(driver, session_id, folder)
    return CheckpointList(
        available=True,
        repository=folder,
        checkpoints=[
            CheckpointResponse(name=c.name, sha=c.sha, created_at=c.created_at) for c in found
        ],
    )


@router.post("/sessions/{session_id}/checkpoints", response_model=CheckpointList)
async def create_checkpoint(
    session_id: str, body: CreateCheckpointRequest, request: Request
) -> CheckpointList:
    """Snapshot the workspace now (including uncommitted work)."""
    driver, folder = _require_workspace(
        await _running_workspace(session_id, request, require_write=True)
    )
    outcome = await git_tools.git_checkpoint(driver, session_id, body.label, folder)
    if outcome.startswith("Error:"):
        raise HTTPException(status_code=500, detail=outcome)
    return await list_checkpoints(session_id, request)


@router.post("/sessions/{session_id}/checkpoints/rewind")
async def rewind_to_checkpoint(
    session_id: str, body: RewindRequest, request: Request
) -> dict[str, str]:
    """Restore the workspace to a checkpoint the platform created."""
    driver, folder = _require_workspace(
        await _running_workspace(session_id, request, require_write=True)
    )
    known = {c.name for c in await git_tools.list_checkpoints(driver, session_id, folder)}
    if body.reference not in known:
        # Only checkpoints, never an arbitrary ref: this endpoint is a rewind, not a reset tool.
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    outcome = await git_tools.git_revert(driver, session_id, body.reference, body.mode, folder)
    if outcome.startswith("Error:"):
        raise HTTPException(status_code=500, detail=outcome)
    return {"status": "restored", "reference": body.reference, "mode": body.mode}
