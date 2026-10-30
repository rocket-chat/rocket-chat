"""A session's whole-workspace snapshots: take one, list them, restore one, delete one."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sandbox_driver.exceptions import SandboxError

from api.auth.session_lookup import session_for_request
from api.services.workspace_snapshots import (
    SnapshotsUnavailable,
    delete_snapshot,
    restore_snapshot,
    snapshots_of,
    take_snapshot,
)
from specifications.interfaces.sandbox import WorkspaceSnapshot

router = APIRouter(prefix="/v1", tags=["Workspace snapshots"])


class SnapshotView(BaseModel):
    id: str
    label: str
    created_at: int
    driver: str
    size_bytes: int | None = None


class SnapshotList(BaseModel):
    snapshots: list[SnapshotView]


class SnapshotRequest(BaseModel):
    label: str = Field(default="Saved from the web app", max_length=120)


def _view(snapshot: WorkspaceSnapshot) -> SnapshotView:
    return SnapshotView(
        id=snapshot.id,
        label=snapshot.label,
        created_at=snapshot.created_at,
        driver=snapshot.driver,
        size_bytes=snapshot.size_bytes,
    )


@router.get("/sessions/{session_id}/snapshots", response_model=SnapshotList)
async def list_snapshots(session_id: str, request: Request) -> SnapshotList:
    """The session's workspace snapshots, oldest first."""
    caller = await session_for_request(request, session_id, require_write=False)
    return SnapshotList(snapshots=[_view(s) for s in snapshots_of(caller.record)])


@router.post("/sessions/{session_id}/snapshots", response_model=SnapshotView, status_code=201)
async def create_snapshot(session_id: str, body: SnapshotRequest, request: Request) -> SnapshotView:
    """Save the whole workspace now (the oldest snapshot past the organization's limit goes)."""
    caller = await session_for_request(request, session_id, require_write=True)
    state = request.app.state
    try:
        snapshot = await take_snapshot(
            getattr(state, "sandbox_driver", None),
            state.session_store,
            getattr(state, "db_manager", None),
            caller.record,
            body.label,
        )
    except SnapshotsUnavailable as err:
        raise HTTPException(status_code=501, detail=str(err)) from err
    except SandboxError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    return _view(snapshot)


@router.post("/sessions/{session_id}/snapshots/{snapshot_id}/restore", response_model=SnapshotView)
async def restore(session_id: str, snapshot_id: str, request: Request) -> SnapshotView:
    """Replace the workspace with a snapshot; the sandbox restarts on it at the next command.
    Refused while a turn runs."""
    caller = await session_for_request(request, session_id, require_write=True)
    if caller.record.is_processing:
        raise HTTPException(status_code=409, detail="Stop the running turn before restoring.")
    try:
        snapshot = await restore_snapshot(
            getattr(request.app.state, "sandbox_driver", None), caller.record, snapshot_id
        )
    except KeyError as err:
        raise HTTPException(status_code=404, detail="Snapshot not found") from err
    except SnapshotsUnavailable as err:
        raise HTTPException(status_code=501, detail=str(err)) from err
    except SandboxError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    return _view(snapshot)


@router.delete("/sessions/{session_id}/snapshots/{snapshot_id}", status_code=204)
async def remove_snapshot(session_id: str, snapshot_id: str, request: Request) -> None:
    """Delete a snapshot and its storage."""
    caller = await session_for_request(request, session_id, require_write=True)
    try:
        await delete_snapshot(
            getattr(request.app.state, "sandbox_driver", None),
            request.app.state.session_store,
            caller.record,
            snapshot_id,
        )
    except KeyError as err:
        raise HTTPException(status_code=404, detail="Snapshot not found") from err
    except SnapshotsUnavailable as err:
        raise HTTPException(status_code=501, detail=str(err)) from err
