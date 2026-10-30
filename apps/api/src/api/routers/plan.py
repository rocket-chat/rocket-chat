"""The plan card: approving, editing or rejecting the plan the agent proposed (``exit_plan_mode``)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.session_lookup import session_for_request
from api.services import plan_approval
from specifications.interfaces.agent import (
    MAX_PLAN_FEEDBACK_CHARS,
    MAX_PLAN_TASK_CHARS,
    MAX_PLAN_TASKS,
    PlanTaskEdit,
)

router = APIRouter(prefix="/v1", tags=["Plans"])

ContinueMode = Literal["ask", "auto_edit", "auto", "bypass"]


class PlanTaskInput(BaseModel):
    id: str = Field(default="", max_length=100)
    title: str = Field(..., min_length=1, max_length=MAX_PLAN_TASK_CHARS)
    description: str = Field(default="", max_length=MAX_PLAN_TASK_CHARS)


class PlanDecisionRequest(BaseModel):
    action_id: str = Field(..., min_length=1, max_length=100, description="The plan's gate")
    approved: bool
    permission_mode: ContinueMode | None = Field(
        default=None, description="The mode the work continues in; required to approve"
    )
    checklist: list[PlanTaskInput] | None = Field(
        default=None,
        max_length=MAX_PLAN_TASKS,
        description="The plan as edited on the card; omit when it was not edited",
    )
    feedback: str = Field(default="", max_length=MAX_PLAN_FEEDBACK_CHARS)


class PlanDecisionResponse(BaseModel):
    action_id: str
    approved: bool
    permission_mode: str = Field(
        ..., description="The session's mode from now on; empty if rejected"
    )
    edited: bool = Field(..., description="The approved plan was edited on the card")


@router.post("/sessions/{session_id}/plan/decision", response_model=PlanDecisionResponse)
async def decide_plan(
    session_id: str, body: PlanDecisionRequest, request: Request
) -> PlanDecisionResponse:
    """Approve (in a chosen permission mode, optionally with an edited plan) or reject the plan.

    Approving changes the session's permission mode, so it takes the session's owner or an
    administrator, and the mode must pass the organization's lock and, for ``bypass``, the
    ``use_bypass_mode`` capability. Any member who may write to the session can reject.
    """
    caller = await session_for_request(request, session_id, require_write=True)
    if body.approved and body.permission_mode is None:
        raise HTTPException(status_code=422, detail="Choose the mode the work continues in.")
    state = request.app.state
    decider = plan_approval.PlanDecider(
        store=state.session_store,
        orchestrator=state.orchestrator,
        event_bus=state.event_bus,
        db_mgr=getattr(state, "db_manager", None),
        user_id=caller.user_id,
        may_change_mode=caller.owns_or_administers,
    )
    choice = plan_approval.PlanDecisionInput(
        action_id=body.action_id,
        approved=body.approved,
        permission_mode=body.permission_mode or "",
        checklist=tuple(
            PlanTaskEdit(id=t.id, title=t.title, description=t.description) for t in body.checklist
        )
        if body.checklist is not None
        else None,
        feedback=body.feedback,
    )
    try:
        decision = await plan_approval.decide_plan(decider, caller.record, choice)
    except plan_approval.PlanDecisionError as err:
        raise HTTPException(status_code=err.status, detail=str(err)) from err
    return PlanDecisionResponse(
        action_id=body.action_id,
        approved=decision.approved,
        permission_mode=decision.permission_mode,
        edited=bool(decision.checklist),
    )
