"""Development-only helpers (404 unless the explicit development bypass is on).

The transcript is server-owned, so clients cannot rewrite it through ``PATCH``. End-to-end
specs still need deterministic history, which this endpoint seeds without a model call.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.auth.access import _check_session_access, _get_request_identity
from api.auth.identity import dev_identity_enabled
from api.services.audit import AuditService
from api.services.proposals import BoundProposals
from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.proposals import ProposalEdit

router = APIRouter(prefix="/v1/dev", tags=["Development"], include_in_schema=False)


class SeedTranscriptRequest(BaseModel):
    messages: list[dict[str, Any]]


@router.post("/sessions/{session_id}/transcript")
async def seed_transcript(
    session_id: str, body: SeedTranscriptRequest, request: Request
) -> dict[str, int]:
    if not dev_identity_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    _, user_id, roles = _get_request_identity(request)
    store = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, user_id, roles, require_write=True)
    record.conversation_history = [
        ChatMessage(
            role=m.get("role", "user"),
            content=m.get("content", ""),
            reasoning=m.get("reasoning"),
            tool_calls=m.get("tool_calls"),
        )
        for m in body.messages
    ]
    await store.update_session(record)
    return {"messages": len(record.conversation_history)}


class SeedProposalRequest(BaseModel):
    target: str = "instructions"
    skill_name: str = ""
    old_text: str
    new_text: str
    summary: str
    rationale: str = ""


@router.post("/agents/{agent_id}/proposals")
async def seed_proposal(
    agent_id: str, body: SeedProposalRequest, request: Request
) -> dict[str, str]:
    """Stage a proposal as the agent's ``propose_change`` would, without a model call."""
    if not dev_identity_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    org_id, user_id, _ = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    outcome = await BoundProposals(
        db_mgr=db_mgr,
        org_id=org_id,
        agent_id=agent_id,
        session_id=None,
        requested_by=user_id,
        audit=AuditService(db_mgr),
    ).propose(
        body.target,
        body.skill_name,
        [ProposalEdit(body.old_text, body.new_text)],
        body.summary,
        body.rationale,
    )
    return {"outcome": outcome}
