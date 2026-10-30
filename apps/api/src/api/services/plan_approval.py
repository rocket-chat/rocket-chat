"""A person's decision on a plan the agent proposed (``exit_plan_mode``): approve, edit or reject.

The plan's approval gate only carries yes or no, and may be held by another replica. So the rest
of the decision (the permission mode the work continues in, an edited checklist, feedback) is
stored on the session record first, under ``metadata.plan_decision``, and the gate is released
after: the waiting tool reads it back through ``TurnCallbacks.plan_decision`` wherever it runs.

The mode is the session's from then on and goes through the same checks as any other choice
of mode: the policy resolver (an organization lock wins) and the ``use_bypass_mode``
capability. Only the session's owner or an administrator may change it, as with the picker.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from api.auth.capabilities import Capability, member_has
from api.services.audit import AuditService
from api.services.turn_service import permission_mode_for
from specifications.interfaces.agent import (
    MAX_PLAN_FEEDBACK_CHARS,
    MAX_PLAN_TASK_CHARS,
    MAX_PLAN_TASKS,
    PERMISSION_MODES,
    PLAN_APPROVAL_ACTION,
    PlanDecision,
    PlanTaskEdit,
)
from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol

METADATA_KEY = "plan_decision"


class PlanDecisionError(Exception):
    """The decision cannot be applied; ``status`` is the HTTP status that says why."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class PlanDecisionInput:
    """What the plan card sent."""

    action_id: str
    approved: bool
    permission_mode: str = ""
    checklist: tuple[PlanTaskEdit, ...] | None = None
    feedback: str = ""


@dataclass(frozen=True)
class PlanDecider:
    """Who decides, and the collaborators the decision goes through."""

    store: SessionStoreProtocol
    orchestrator: Any
    event_bus: EventBusProtocol
    db_mgr: Any
    user_id: str
    may_change_mode: bool


def _cut(text: str, limit: int) -> str:
    return text.strip()[:limit]


def bounded_decision(choice: PlanDecisionInput, decided_at: float) -> PlanDecision:
    """The decision as stored: every field within the ``MAX_PLAN_*`` bounds."""
    tasks = tuple(
        PlanTaskEdit(
            id=_cut(task.id, 100) or str(index),
            title=_cut(task.title, MAX_PLAN_TASK_CHARS),
            description=_cut(task.description, MAX_PLAN_TASK_CHARS),
        )
        for index, task in enumerate((choice.checklist or ())[:MAX_PLAN_TASKS], start=1)
        if task.title.strip()
    )
    return PlanDecision(
        approved=choice.approved,
        permission_mode=choice.permission_mode if choice.approved else "",
        checklist=tasks if choice.approved else (),
        feedback=_cut(choice.feedback, MAX_PLAN_FEEDBACK_CHARS),
        decided_at=decided_at,
    )


async def check_mode(db_mgr: Any, org_id: str, user_id: str, mode: str) -> None:
    """Refuse a mode the member may not choose: locked by the organization, or bypass without
    the capability. The same resolver the turn uses decides."""
    if mode not in PERMISSION_MODES or mode == "plan":
        raise PlanDecisionError(422, "Choose the mode the work continues in (not read-only).")
    resolved = await permission_mode_for(db_mgr, org_id, user_id, None, mode)
    if resolved == mode:
        return
    if mode == "bypass" and not await member_has(
        db_mgr, org_id, user_id, Capability.USE_BYPASS_MODE
    ):
        raise PlanDecisionError(403, "Your role does not have the use_bypass_mode capability.")
    raise PlanDecisionError(
        409, f"Your organization locked the permission mode to '{resolved}'; choose that one."
    )


def _stored(action_id: str, decision: PlanDecision, decided_by: str) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "approved": decision.approved,
        "permission_mode": decision.permission_mode,
        "checklist": [
            {"id": t.id, "title": t.title, "description": t.description} for t in decision.checklist
        ],
        "feedback": decision.feedback,
        "decided_at": decision.decided_at,
        "decided_by": decided_by,
    }


def _tasks_of(decision: PlanDecision) -> list[dict[str, Any]]:
    return [
        {"id": t.id, "title": t.title, "status": "pending", "description": t.description}
        for t in decision.checklist
    ]


def _record_decision(
    action_id: str, decision: PlanDecision, decided_by: str
) -> Callable[[SessionRecord], None]:
    def record(target: SessionRecord) -> None:
        metadata = dict(target.metadata) if isinstance(target.metadata, dict) else {}
        metadata[METADATA_KEY] = _stored(action_id, decision, decided_by)
        if decision.permission_mode:
            metadata["permission_mode"] = decision.permission_mode
        if decision.checklist:
            target.checklist = _tasks_of(decision)
            metadata["checklist"] = target.checklist
        target.metadata = metadata

    return record


def _forget_decision(action_id: str, previous_mode: Any) -> Callable[[SessionRecord], None]:
    """Undo a decision no gate took: the mode goes back to what it was."""

    def forget(target: SessionRecord) -> None:
        metadata = dict(target.metadata) if isinstance(target.metadata, dict) else {}
        stored = metadata.get(METADATA_KEY)
        if not isinstance(stored, dict) or stored.get("action_id") != action_id:
            return
        metadata.pop(METADATA_KEY, None)
        if previous_mode in PERMISSION_MODES:
            metadata["permission_mode"] = previous_mode
        else:
            metadata.pop("permission_mode", None)
        target.metadata = metadata

    return forget


async def decide_plan(
    decider: PlanDecider, record: SessionRecord, choice: PlanDecisionInput
) -> PlanDecision:
    """Store the decision, release the plan's gate, and tell every participant."""
    org_id, session_id = record.tenant_org_id, record.session_id
    if choice.approved:
        if not decider.may_change_mode:
            raise PlanDecisionError(
                403, "Only the session's owner or an administrator can approve its plan."
            )
        await check_mode(decider.db_mgr, org_id, decider.user_id, choice.permission_mode)
    decision = bounded_decision(choice, time.time())
    previous_mode = (record.metadata or {}).get("permission_mode")
    await decider.store.mutate_session(
        session_id, org_id, _record_decision(choice.action_id, decision, decider.user_id)
    )
    try:
        await decider.orchestrator.submit_human_approval(
            session_id=session_id, action_id=choice.action_id, approved=decision.approved
        )
    except KeyError as err:
        await decider.store.mutate_session(
            session_id, org_id, _forget_decision(choice.action_id, previous_mode)
        )
        raise PlanDecisionError(404, "No plan is waiting for this decision any more.") from err
    await _announce(decider, record, choice.action_id, decision)
    return decision


async def _announce(
    decider: PlanDecider, record: SessionRecord, action_id: str, decision: PlanDecision
) -> None:
    session_id = record.session_id
    if decision.checklist:
        await decider.event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.PLAN_UPDATED,
                payload={"session_id": session_id, "tasks": _tasks_of(decision)},
            )
        )
    await decider.event_bus.publish(
        SessionEvent(
            session_id=session_id,
            event_type=EventType.PLAN_DECIDED,
            payload={
                "action_id": action_id,
                "approved": decision.approved,
                "permission_mode": decision.permission_mode,
                "edited": bool(decision.checklist),
                "decided_by": decider.user_id,
            },
        )
    )
    await AuditService(decider.db_mgr).record_approval_decision(
        org_id=record.tenant_org_id,
        actor_user_id=decider.user_id,
        session_id=session_id,
        action_type=PLAN_APPROVAL_ACTION,
        risk_level="medium",
        approved=decision.approved,
        approved_by=decider.user_id,
    )


def _tasks_from(raw: Any) -> tuple[PlanTaskEdit, ...]:
    items: Sequence[Any] = raw if isinstance(raw, list) else []
    return tuple(
        PlanTaskEdit(
            id=str(item.get("id") or ""),
            title=str(item.get("title") or "")[:MAX_PLAN_TASK_CHARS],
            description=str(item.get("description") or "")[:MAX_PLAN_TASK_CHARS],
        )
        for item in items[:MAX_PLAN_TASKS]
        if isinstance(item, dict)
    )


def plan_decision_of(metadata: Any, since: float) -> PlanDecision | None:
    """The decision stored on the session, if it was made at or after ``since``."""
    stored = metadata.get(METADATA_KEY) if isinstance(metadata, dict) else None
    if not isinstance(stored, dict):
        return None
    decided_at = float(stored.get("decided_at") or 0.0)
    if decided_at < since:
        return None
    return PlanDecision(
        approved=bool(stored.get("approved")),
        permission_mode=str(stored.get("permission_mode") or ""),
        checklist=_tasks_from(stored.get("checklist")),
        feedback=str(stored.get("feedback") or "")[:MAX_PLAN_FEEDBACK_CHARS],
        decided_at=decided_at,
    )


def plan_decision_reader(
    store: SessionStoreProtocol, db_mgr: Any, org_id: str, requester_id: str
) -> Callable[[str, float], Awaitable[PlanDecision | None]]:
    """``TurnCallbacks.plan_decision`` for one turn.

    The stored mode is resolved again for the turn's requester, so a lock set or a capability
    withdrawn since the decision still applies (the turn's bypass is the requester's).
    """

    async def read(session_id: str, since: float) -> PlanDecision | None:
        record = await store.get_session(session_id)
        decision = plan_decision_of(record.metadata if record else None, since)
        if decision is None or not decision.permission_mode:
            return decision
        mode = await permission_mode_for(
            db_mgr, org_id, requester_id, None, decision.permission_mode
        )
        return PlanDecision(
            approved=decision.approved,
            permission_mode=mode,
            checklist=decision.checklist,
            feedback=decision.feedback,
            decided_at=decision.decided_at,
        )

    return read
