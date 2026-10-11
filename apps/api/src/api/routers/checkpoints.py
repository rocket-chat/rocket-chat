"""Workspace checkpoints: list, create and rewind.

The agent snapshots the workspace before a turn first changes it (and on request); this is
the person's side of that safety net. A rewind is an explicit act by someone with write
access to the session, so it does not go through the agent's approval gate.
"""

from __future__ import annotations

from agent_core.tools import git_tools
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _check_session_access, _get_request_identity
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
        ..., description="False when the sandbox is not running or the workspace is not a git repo"
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
) -> SandboxDriverProtocol | None:
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


def _require_driver(driver: SandboxDriverProtocol | None) -> SandboxDriverProtocol:
    if driver is None:
        raise HTTPException(
            status_code=409,
            detail="The sandbox is not running; send a message to start it, then try again.",
        )
    return driver


@router.get("/sessions/{session_id}/checkpoints", response_model=CheckpointList)
async def list_checkpoints(session_id: str, request: Request) -> CheckpointList:
    """Checkpoints of the session's workspace, newest first."""
    driver = await _running_workspace(session_id, request, require_write=False)
    if driver is None or not await git_tools.is_git_workspace(driver, session_id):
        return CheckpointList(available=False)
    found = await git_tools.list_checkpoints(driver, session_id)
    return CheckpointList(
        available=True,
        checkpoints=[
            CheckpointResponse(name=c.name, sha=c.sha, created_at=c.created_at) for c in found
        ],
    )


@router.post("/sessions/{session_id}/checkpoints", response_model=CheckpointList)
async def create_checkpoint(
    session_id: str, body: CreateCheckpointRequest, request: Request
) -> CheckpointList:
    """Snapshot the workspace now (including uncommitted work)."""
    driver = _require_driver(await _running_workspace(session_id, request, require_write=True))
    if not await git_tools.is_git_workspace(driver, session_id):
        raise HTTPException(status_code=409, detail="The workspace is not a git repository.")
    outcome = await git_tools.git_checkpoint(driver, session_id, body.label)
    if outcome.startswith("Error:"):
        raise HTTPException(status_code=500, detail=outcome)
    return await list_checkpoints(session_id, request)


@router.post("/sessions/{session_id}/checkpoints/rewind")
async def rewind_to_checkpoint(
    session_id: str, body: RewindRequest, request: Request
) -> dict[str, str]:
    """Restore the workspace to a checkpoint the platform created."""
    driver = _require_driver(await _running_workspace(session_id, request, require_write=True))
    known = {c.name for c in await git_tools.list_checkpoints(driver, session_id)}
    if body.reference not in known:
        # Only checkpoints, never an arbitrary ref: this endpoint is a rewind, not a reset tool.
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    outcome = await git_tools.git_revert(driver, session_id, body.reference, body.mode)
    if outcome.startswith("Error:"):
        raise HTTPException(status_code=500, detail=outcome)
    return {"status": "restored", "reference": body.reference, "mode": body.mode}
