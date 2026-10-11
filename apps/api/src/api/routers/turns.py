"""Turn-trace inspection API.

The cockpit's turn inspector needs the persisted trace of every completed turn —
tool calls, touched files, approval gates, outcome — without re-deriving it from the
streamed transcript. Reads are tenant-scoped twice over: the session ACL is enforced
here and PostgreSQL RLS re-filters the rows by the caller's organization.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.auth.access import _check_session_access, _get_request_identity
from api.db.session import DatabaseSessionManager
from api.db.store import TurnTraceRecord, TurnTraceStore
from specifications.interfaces.session import SessionStoreProtocol

router = APIRouter(prefix="/v1", tags=["Turn Traces"])


class TurnTraceResponse(BaseModel):
    """One persisted turn: what the agent did, what it touched, and how it ended."""

    id: str = Field(..., description="Trace identifier")
    session_id: str = Field(..., description="Session the turn belongs to")
    turn_index: int = Field(..., description="1-based turn number within the session")
    started_at: int = Field(..., description="Turn start, epoch seconds")
    ended_at: int | None = Field(
        default=None, description="Turn end, epoch seconds; null while a turn is still running"
    )
    status: str = Field(
        ...,
        description=(
            "'running' while in flight, 'completed' or 'error' when it ended normally, "
            "'interrupted' when the process died mid-turn and recovery marked it"
        ),
    )
    reasoning_summary: str | None = Field(
        default=None, description="Bounded summary of the turn's reasoning stream"
    )
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list, description="Tool calls executed, with status and duration"
    )
    diff_paths: list[str] = Field(default_factory=list, description="Files the turn touched")
    approvals: list[dict[str, Any]] = Field(
        default_factory=list, description="Human approval gates the turn paused on"
    )
    attachments: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Files attached to the turn (id, name, mime, size), by reference",
    )
    tokens: dict[str, Any] = Field(
        default_factory=dict, description="Provider-reported token usage when available"
    )
    error: str | None = Field(default=None, description="Failure reason for an errored turn")
    resume_attempt: int = Field(
        default=0,
        description="Greater than 0 when the platform continued this task after a restart",
    )


class TurnTracePage(BaseModel):
    """One page of a session's turn traces, oldest turn first."""

    session_id: str = Field(..., description="Session the traces belong to")
    total: int = Field(..., description="Total traces persisted for the session")
    limit: int = Field(..., description="Requested page size")
    offset: int = Field(..., description="Requested page offset")
    turns: list[TurnTraceResponse] = Field(default_factory=list, description="Traces in the page")


def _to_response(trace: TurnTraceRecord) -> TurnTraceResponse:
    return TurnTraceResponse(
        id=trace.id,
        session_id=trace.session_id,
        turn_index=trace.turn_index,
        started_at=trace.started_at,
        ended_at=trace.ended_at,
        status=trace.status,
        reasoning_summary=trace.reasoning_summary,
        tool_calls=trace.tool_calls,
        diff_paths=trace.diff_paths,
        approvals=trace.approvals,
        attachments=trace.attachments,
        tokens=trace.tokens,
        error=trace.error,
        resume_attempt=trace.resume_attempt,
    )


@router.get("/sessions/{session_id}/turns", response_model=TurnTracePage)
async def list_session_turns(
    session_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100, description="Page size"),
    offset: int = Query(default=0, ge=0, description="Page offset, oldest turn first"),
) -> TurnTracePage:
    """List the persisted turn traces of one session for the cockpit turn inspector."""
    db_manager: DatabaseSessionManager | None = getattr(request.app.state, "db_manager", None)
    if db_manager is None:
        # Traces are durable rows, so without PostgreSQL there is nothing to inspect:
        # an empty 200 would look like "this session ran no turns".
        raise HTTPException(
            status_code=503,
            detail="Turn traces require the PostgreSQL-backed session store",
        )

    org_id, user_id, roles = _get_request_identity(request)
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, user_id, roles)

    trace_store = TurnTraceStore(db_manager)
    traces = await trace_store.list_for_session(session_id, org_id, limit=limit, offset=offset)
    total = await trace_store.count_for_session(session_id, org_id)
    return TurnTracePage(
        session_id=session_id,
        total=total,
        limit=limit,
        offset=offset,
        turns=[_to_response(trace) for trace in traces],
    )
