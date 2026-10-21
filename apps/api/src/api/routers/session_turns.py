"""Starting turns and answering a running turn's gates over HTTP."""

import logging
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from git_engine.engine import GitEngine
from pydantic import BaseModel, Field

from api.auth.access import _check_session_access, _get_request_identity
from api.models import (
    AnswerQuestionRequest,
    ApprovalRequestModel,
    UserTurnRequest,
)
from api.services.audit import (
    AuditService,
)
from api.services.metrics import (
    record_quota_rejection,
)
from api.services.queued_messages import QueuedMessageStore, hold_for_running_turn
from api.services.session_support import _new_message_id
from api.services.waits import get_wait_store, settle_waits
from api.turns.context import TurnSelection
from api.turns.gates import _forget_pending_decisions
from api.turns.shared import _api_rate_limiter
from api.turns.tasks import _spawn_background_turn
from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent
from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionStoreProtocol

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])


@router.post("/sessions/{session_id}/turns")
async def start_session_turn(
    session_id: str,
    req: UserTurnRequest,
    request: Request,
) -> dict[str, str]:
    """Dispatch user prompt to the ReAct engine in the background."""
    store: SessionStoreProtocol = request.app.state.session_store
    orchestrator: AsyncReActOrchestrator = request.app.state.orchestrator
    event_bus: EventBusProtocol = request.app.state.event_bus
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)
    registry: ToolRegistry | None = getattr(request.app.state, "tool_registry", None)
    git_engine: GitEngine | None = getattr(request.app.state, "git_engine", None)
    db_mgr = getattr(request.app.state, "db_manager", None)
    attachment_service = getattr(request.app.state, "attachment_service", None)

    req_org, req_user, roles = _get_request_identity(request)
    # Authenticated-path rate limit: bound turns per user and per
    # org before any work is dispatched, so an abusive caller cannot exhaust the pod or
    # the model budget. The Slack path has its own equivalent gate.
    limit_decision, limit_scope = _api_rate_limiter.check(org_id=req_org, user_id=req_user)
    if not limit_decision.allowed:
        record_quota_rejection(limit_scope or "api_user")
        raise HTTPException(
            status_code=429,
            detail=(
                "Rate limit exceeded for this "
                f"{'organization' if limit_scope == 'api_org' else 'user'}; retry shortly."
            ),
            headers={"Retry-After": str(limit_decision.retry_after_seconds)},
        )

    record = await store.get_session(session_id)
    if not record:
        # Sessions are created by POST /v1/sessions, which applies the caller's organization,
        # its privacy default and the sandbox choice; a turn never creates one implicitly.
        raise HTTPException(
            status_code=404, detail="Session not found: create it with POST /v1/sessions first"
        )
    _check_session_access(record, req_user, roles, require_write=True)

    message_id = _new_message_id()
    if not req.attachment_ids and await hold_for_running_turn(
        session_store=store,
        orchestrator=orchestrator,
        event_bus=event_bus,
        db_mgr=db_mgr,
        org_id=record.tenant_org_id,
        session_id=session_id,
        prompt=req.prompt,
        delivery=req.delivery,
        message_id=message_id,
        author_user_id=req_user,
    ):
        status = "steering" if req.delivery == "steer" else "queued"
        return {"status": status, "session_id": session_id, "message_id": message_id}
    _spawn_background_turn(
        session_id=session_id,
        prompt=req.prompt,
        orchestrator=orchestrator,
        event_bus=event_bus,
        driver=driver,
        registry=registry,
        store=store,
        model=req.model,
        git_engine=git_engine,
        db_mgr=db_mgr,
        message_id=message_id,
        author_user_id=req_user,
        attachment_service=attachment_service,
        attachment_ids=req.attachment_ids,
        concurrency_gate=getattr(request.app.state, "global_concurrency_gate", None),
        session_locks=getattr(request.app.state, "session_write_locks", None),
        selection=(
            TurnSelection(agent_id=req.agent_id, raw=req.mode == "direct")
            if req.mode is not None or req.agent_id
            else None
        ),
    )
    return {"status": "started", "session_id": session_id, "message_id": message_id}


@router.post("/sessions/{session_id}/questions/{question_id}")
async def answer_question(
    session_id: str,
    question_id: str,
    req: AnswerQuestionRequest,
    request: Request,
) -> dict[str, Any]:
    """Submit choice or write-in to an in-stream Interactive Decision Card."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    _, req_user, roles = _get_request_identity(request)
    _check_session_access(record, req_user, roles, require_write=True)

    orchestrator: AsyncReActOrchestrator = request.app.state.orchestrator
    event_bus: EventBusProtocol = request.app.state.event_bus

    try:
        await orchestrator.submit_question_answer(
            session_id=session_id,
            question_id=question_id,
            selected_options=req.selected_options,
            custom_text=req.custom_text,
        )
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.DECISION_SUBMITTED,
                payload={
                    "question_id": question_id,
                    "selected_options": req.selected_options,
                    "custom_text": req.custom_text,
                },
            )
        )
        await _forget_pending_decisions(
            getattr(request.app.state, "db_manager", None),
            session_id,
            record.tenant_org_id,
        )
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err

    return {"status": "submitted", "question_id": question_id}


@router.post("/sessions/{session_id}/approvals/{action_id}")
async def submit_approval(
    session_id: str,
    action_id: str,
    req: ApprovalRequestModel,
    request: Request,
) -> dict[str, Any]:
    """Approve or deny an agent confirmation gate."""
    store: SessionStoreProtocol = request.app.state.session_store
    org_id, req_user, roles = _get_request_identity(request)
    record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, req_user, roles, require_write=True)

    orchestrator: AsyncReActOrchestrator = request.app.state.orchestrator
    try:
        await orchestrator.submit_human_approval(
            session_id=session_id,
            action_id=action_id,
            approved=req.approved,
        )
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err

    # Approval trail: who approved or denied, for which session.
    await AuditService(getattr(request.app.state, "db_manager", None)).record_approval_decision(
        org_id=org_id,
        actor_user_id=req_user,
        session_id=session_id,
        action_type=None,
        risk_level=None,
        approved=req.approved,
        approved_by=req_user,
    )

    return {"status": "recorded", "action_id": action_id, "approved": req.approved}


@router.delete("/sessions/{session_id}/turns")
async def cancel_turn(session_id: str, request: Request) -> dict[str, str]:
    """Halt current autonomous execution immediately."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    _, req_user, roles = _get_request_identity(request)
    _check_session_access(record, req_user, roles, require_write=True)

    orchestrator: AsyncReActOrchestrator = request.app.state.orchestrator
    await orchestrator.cancel_turn(session_id)
    # Stopping means stopping: a pending wait would otherwise restart the work at its deadline.
    await settle_waits(
        getattr(request.app.state, "wait_store", None)
        or get_wait_store(getattr(request.app.state, "db_manager", None)),
        session_id,
        record.tenant_org_id,
        status="cancelled",
    )
    event_bus: EventBusProtocol | None = getattr(request.app.state, "event_bus", None)
    if event_bus:
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.STATUS_CHANGED,
                payload={"status": "cancelled", "message": "Turn interrupted by user."},
            )
        )
    return {"status": "cancelled", "session_id": session_id}


class PendingMessageResponse(BaseModel):
    message_id: str = Field(..., description="The message's id")
    delivery: str = Field(..., description="steer (next step of the running turn) or queue")
    content: str = Field(..., description="What the person wrote")
    author_user_id: str | None = Field(default=None, description="Who sent it")


@router.get("/sessions/{session_id}/queued-messages", response_model=list[PendingMessageResponse])
async def list_queued_messages(session_id: str, request: Request) -> list[PendingMessageResponse]:
    """Messages sent during a turn that no turn has taken yet, oldest first."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    _, req_user, roles = _get_request_identity(request)
    _check_session_access(record, req_user, roles)
    pending = await QueuedMessageStore(getattr(request.app.state, "db_manager", None)).pending(
        record.tenant_org_id, session_id
    )
    return [
        PendingMessageResponse(
            message_id=m.id,
            delivery=m.delivery,
            content=m.content,
            author_user_id=m.author_user_id,
        )
        for m in pending
    ]


@router.delete("/sessions/{session_id}/queued-messages/{message_id}")
async def cancel_queued_message(
    session_id: str, message_id: str, request: Request
) -> dict[str, str]:
    """Withdraw a message sent during a turn (steering it or queued after it) that no turn has
    taken yet."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    _, req_user, roles = _get_request_identity(request)
    _check_session_access(record, req_user, roles, require_write=True)
    queued = QueuedMessageStore(getattr(request.app.state, "db_manager", None))
    if not await queued.cancel(record.tenant_org_id, session_id, message_id):
        raise HTTPException(status_code=404, detail="No pending message with this id")
    orchestrator: AsyncReActOrchestrator = request.app.state.orchestrator
    orchestrator.withdraw_steering(session_id, message_id)
    event_bus: EventBusProtocol | None = getattr(request.app.state, "event_bus", None)
    if event_bus:
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.MESSAGE_CANCELLED,
                payload={"message_id": message_id},
            )
        )
    return {"status": "cancelled", "message_id": message_id}
