"""Per-file diff application API (per-file accept/reject, apply-to-workspace).

The cockpit renders each proposed file diff from a multi-file patch streamed by the
agent. Clicking "Apply this file" is the human approval for that one file; this endpoint
applies exactly that file's hunks to the session workspace and broadcasts the resulting
``file_diff`` so every other tab converges. The patch engine is the same one the agent
uses (`agent_core.tools.tier2`), so a cockpit-initiated apply and an agent-initiated
apply behave identically.
"""

from __future__ import annotations

from dataclasses import asdict

from agent_core.tools import tier2
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _check_session_access, _get_request_identity
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.session import SessionStoreProtocol

router = APIRouter(prefix="/v1", tags=["Diffs"])


class ApplyDiffRequest(BaseModel):
    """One file's section of a multi-file unified diff, as rendered in the cockpit."""

    path: str = Field(..., min_length=1, description="Workspace-relative path to apply")
    diff_content: str = Field(
        ..., min_length=1, description="Unified diff; only hunks for `path` are applied"
    )


class ApplyDiffResponse(BaseModel):
    """Outcome of applying one file's diff."""

    status: str = Field(..., description="'applied' on success")
    path: str = Field(..., description="The file that was patched")
    message: str = Field(..., description="Human-readable result, including the failure cause")
    additions: int = Field(default=0, description="Lines added by the applied patch")
    deletions: int = Field(default=0, description="Lines removed by the applied patch")


@router.post("/sessions/{session_id}/diffs/apply", response_model=ApplyDiffResponse)
async def apply_session_diff(
    session_id: str,
    body: ApplyDiffRequest,
    request: Request,
) -> ApplyDiffResponse:
    """Apply one file's hunks of a proposed multi-file diff to the session workspace.

    The caller's click is the approval; the request is still authenticated and ACL-checked
    against the session, and RLS scopes the workspace to the caller's organization.
    """
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _, user_id, roles = _get_request_identity(request)
    _check_session_access(record, user_id, roles)

    driver = getattr(request.app.state, "sandbox_driver", None)
    if driver is None:
        raise HTTPException(status_code=503, detail="Sandbox driver is not available")

    message, diff = await tier2.apply_diff_file(driver, session_id, body.diff_content, body.path)
    if diff is None:
        # No matching section, or the hunks did not apply cleanly. The engine's message
        # says which; surface it rather than a generic failure.
        raise HTTPException(status_code=422, detail=message)

    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus is not None and hasattr(event_bus, "publish"):
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.FILE_DIFF,
                payload=asdict(diff),
            )
        )

    return ApplyDiffResponse(
        status="applied",
        path=body.path,
        message=message,
        additions=diff.additions,
        deletions=diff.deletions,
    )
