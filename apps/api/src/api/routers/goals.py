"""Session goals, durable waits, and manual context compaction API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.auth.access import _check_session_access, _get_request_identity
from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent
from specifications.interfaces.session import GoalStatus, SessionStoreProtocol

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Goals & Waits"])


class GoalResponse(BaseModel):
    id: str
    session_id: str
    description: str
    status: str
    max_wall_time_seconds: int | None = None
    max_total_tokens: int | None = None
    max_cost_usd: float | None = None
    max_continuations: int | None = None
    consumed_tokens: int = 0
    consumed_cost_usd: float = 0.0
    continuation_count: int = 0
    stop_condition: str | None = None
    created_at: int | None = None
    updated_at: int | None = None
    completed_at: int | None = None


class WaitResponse(BaseModel):
    id: str
    session_id: str
    kind: str
    target: str
    deadline: int
    status: str
    wake_prompt: str | None = None
    created_at: int
    claimed_at: int | None = None
    completed_at: int | None = None


class CompactionSummaryResponse(BaseModel):
    id: str
    session_id: str
    boundary_seq: int
    summary: str | None
    tokens_before: int
    tokens_after: int
    created_at: int


class CompactSessionResponse(BaseModel):
    status: str
    tokens_before: int = 0
    tokens_after: int = 0
    compacted_messages_count: int = 0
    message: str | None = None


@router.get("/sessions/{session_id}/goals", response_model=GoalResponse)
async def get_session_goal(session_id: str, request: Request) -> GoalResponse:
    """Retrieve the current autonomous goal and budget caps for a session."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    req_org, req_user, roles = _get_request_identity(request)
    _check_session_access(record, req_user, roles, require_write=False)

    goal_store = getattr(request.app.state, "goal_store", None)
    if not goal_store:
        raise HTTPException(status_code=404, detail="No goal store configured")

    goal = await goal_store.get_goal(session_id, req_org)
    if not goal:
        raise HTTPException(status_code=404, detail="No active goal for this session")

    return GoalResponse(
        id=goal.id,
        session_id=goal.session_id,
        description=goal.description,
        status=goal.status.value,
        max_wall_time_seconds=goal.max_wall_time_seconds,
        max_total_tokens=goal.max_total_tokens,
        max_cost_usd=goal.max_cost_usd,
        max_continuations=goal.max_continuations,
        consumed_tokens=goal.consumed_tokens,
        consumed_cost_usd=goal.consumed_cost_usd,
        continuation_count=goal.continuation_count,
        stop_condition=goal.stop_condition,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
        completed_at=goal.completed_at,
    )


@router.post("/sessions/{session_id}/goals/cancel")
async def cancel_session_goal(session_id: str, request: Request) -> dict[str, str]:
    """Halt an autonomous goal and its continuation chain immediately."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    req_org, req_user, roles = _get_request_identity(request)
    _check_session_access(record, req_user, roles, require_write=True)

    goal_store = getattr(request.app.state, "goal_store", None)
    if not goal_store:
        raise HTTPException(status_code=404, detail="No goal store configured")

    goal = await goal_store.get_goal(session_id, req_org)
    if not goal:
        raise HTTPException(status_code=404, detail="No goal found for this session")

    goal.status = GoalStatus.CANCELLED
    await goal_store.update_goal(goal)

    event_bus: EventBusProtocol | None = getattr(request.app.state, "event_bus", None)
    if event_bus:
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.STATUS_CHANGED,
                payload={"status": "cancelled", "message": "Goal cancelled by user."},
            )
        )
    return {"status": "cancelled", "session_id": session_id}


@router.get("/sessions/{session_id}/waits", response_model=list[WaitResponse])
async def list_session_waits(session_id: str, request: Request) -> list[WaitResponse]:
    """List active asynchronous waits (time, process, CI, or review) for a session."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    req_org, req_user, roles = _get_request_identity(request)
    _check_session_access(record, req_user, roles, require_write=False)

    wait_store = getattr(request.app.state, "wait_store", None)
    if not wait_store:
        return []

    waits = await wait_store.list_active_waits(session_id, req_org)
    return [
        WaitResponse(
            id=w.id,
            session_id=w.session_id,
            kind=w.kind,
            target=w.target,
            deadline=w.deadline,
            status=w.status,
            wake_prompt=w.wake_prompt,
            created_at=w.created_at,
            claimed_at=w.claimed_at,
            completed_at=w.completed_at,
        )
        for w in waits
    ]


@router.post("/sessions/{session_id}/waits/{wait_id}/cancel")
async def cancel_session_wait(session_id: str, wait_id: str, request: Request) -> dict[str, str]:
    """Cancel a pending wait and release any associated timers or watches."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    req_org, req_user, roles = _get_request_identity(request)
    _check_session_access(record, req_user, roles, require_write=True)

    wait_store = getattr(request.app.state, "wait_store", None)
    if not wait_store:
        raise HTTPException(status_code=404, detail="No wait store configured")

    resolved = await wait_store.resolve_wait(session_id, wait_id, req_org, status="cancelled")
    if not resolved:
        raise HTTPException(status_code=404, detail="Wait not found")

    event_bus: EventBusProtocol | None = getattr(request.app.state, "event_bus", None)
    if event_bus:
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.WAIT_COMPLETED,
                payload={
                    "wait_id": wait_id,
                    "wait_type": resolved.kind,
                    "target": resolved.target,
                    "status": "cancelled",
                    "summary": f"Wait {wait_id} was cancelled by user.",
                },
            )
        )
    return {"status": "cancelled", "wait_id": wait_id}


@router.post("/sessions/{session_id}/compact", response_model=CompactSessionResponse)
async def trigger_session_compaction(session_id: str, request: Request) -> CompactSessionResponse:
    """Manually compact a session's history into a durable summary checkpoint."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    req_org, req_user, roles = _get_request_identity(request)
    _check_session_access(record, req_user, roles, require_write=True)

    orchestrator = getattr(request.app.state, "orchestrator", None)
    if not orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator not available")

    result = await orchestrator.compact_history(session_id, req_org)
    if not result:
        return CompactSessionResponse(
            status="skipped", message="Session history not eligible for compaction"
        )

    event_bus: EventBusProtocol | None = getattr(request.app.state, "event_bus", None)
    if event_bus:
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.CONTEXT_COMPACTED,
                payload={
                    "tokens_before": getattr(result, "tokens_before", 0),
                    "tokens_after": getattr(result, "tokens_after", 0),
                    "compacted_messages_count": getattr(result, "compacted_count", 0),
                    "summary": getattr(result, "summary", None),
                },
            )
        )
    return CompactSessionResponse(
        status="compacted",
        tokens_before=getattr(result, "tokens_before", 0),
        tokens_after=getattr(result, "tokens_after", 0),
        compacted_messages_count=getattr(result, "compacted_count", 0),
    )


@router.get("/sessions/{session_id}/compactions", response_model=list[CompactionSummaryResponse])
async def list_session_compactions(
    session_id: str, request: Request
) -> list[CompactionSummaryResponse]:
    """List historical compaction checkpoints for a session."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    req_org, req_user, roles = _get_request_identity(request)
    _check_session_access(record, req_user, roles, require_write=False)

    compaction_store = getattr(request.app.state, "compaction_store", None)
    if not compaction_store or not hasattr(compaction_store, "list_compactions"):
        return []

    checkpoints = await compaction_store.list_compactions(session_id, req_org)
    return [
        CompactionSummaryResponse(
            id=cp.id,
            session_id=cp.session_id,
            boundary_seq=cp.boundary_seq,
            summary=cp.summary,
            tokens_before=cp.tokens_before,
            tokens_after=cp.tokens_after,
            created_at=cp.created_at,
        )
        for cp in checkpoints
    ]
