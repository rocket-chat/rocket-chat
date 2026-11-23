"""Reviewing the changes agents propose to their instructions and skills.

Every route answers only for proposals the caller may decide: the people who may edit the
proposal's agent or skill (its owner, its team's administrators, or the organization's). An
approval saves the change under the caller's own rights.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.db.session import DatabaseSessionManager
from api.services.audit import AuditService
from api.services.ownership import request_viewer
from api.services.proposals import (
    ProposalError,
    approve,
    reject,
    reviewable,
    reviewable_one,
)
from specifications.interfaces.proposals import ChangeProposal

router = APIRouter(prefix="/v1", tags=["Agents"])


class ProposalEditModel(BaseModel):
    old_text: str = Field(description="Text the edit replaced")
    new_text: str = Field(description="What it became")


class ProposalResponse(BaseModel):
    """A change an agent proposed to its instructions or one of its skills."""

    id: str = Field(description="Proposal identifier")
    agent_id: str = Field(description="The agent that proposed it")
    target_kind: str = Field(description="'instructions' or 'skill'")
    target_id: str = Field(description="The agent id, or the skill id")
    target_name: str = Field(description="The agent's or the skill's name")
    summary: str = Field(description="One line: what changes")
    rationale: str = Field(description="Why, as the agent explained it")
    status: str = Field(description="'pending', 'approved' or 'rejected'")
    session_id: str | None = Field(default=None, description="The session it was proposed in")
    requested_by: str = Field(description="Who the agent was answering when it proposed it")
    created_at: int = Field(description="Epoch seconds")
    decided_by: str | None = Field(default=None, description="Who decided it")
    decided_at: int | None = Field(default=None, description="When it was decided")
    decision_note: str = Field(default="", description="The reviewer's note")
    change_request_url: str = Field(
        default="",
        description="For a skill that follows an external source: the pull request approving "
        "it opened",
    )
    edits: list[ProposalEditModel] = Field(default_factory=list, description="The edits")
    base_content: str = Field(default="", description="The text the edits were made on")
    proposed_content: str = Field(
        default="", description="The text the edits produce (once approved: what was saved)"
    )


class ProposalListResponse(BaseModel):
    proposals: list[ProposalResponse] = Field(description="Newest first, without their texts")


class ApproveRequest(BaseModel):
    content: str | None = Field(
        default=None,
        max_length=200_000,
        description="The text to save instead of the proposed one (an edited approval)",
    )
    note: str = Field(default="", max_length=2_000, description="A note for the agent")


class RejectRequest(BaseModel):
    note: str = Field(default="", max_length=2_000, description="Why, for the agent")


def _response(proposal: ChangeProposal, *, with_texts: bool) -> ProposalResponse:
    return ProposalResponse(
        id=proposal.id,
        agent_id=proposal.agent_id,
        target_kind=proposal.target_kind,
        target_id=proposal.target_id,
        target_name=proposal.target_name,
        summary=proposal.summary,
        rationale=proposal.rationale,
        status=proposal.status,
        session_id=proposal.session_id,
        requested_by=proposal.requested_by,
        created_at=proposal.created_at,
        decided_by=proposal.decided_by,
        decided_at=proposal.decided_at,
        decision_note=proposal.decision_note,
        change_request_url=proposal.change_request_url,
        edits=[ProposalEditModel(old_text=e.old_text, new_text=e.new_text) for e in proposal.edits]
        if with_texts
        else [],
        base_content=proposal.base_content if with_texts else "",
        proposed_content=proposal.proposed_content if with_texts else "",
    )


def _db(request: Request) -> DatabaseSessionManager | None:
    db_mgr: DatabaseSessionManager | None = getattr(request.app.state, "db_manager", None)
    return db_mgr


@router.get("/proposals", response_model=ProposalListResponse)
async def list_proposals(
    request: Request,
    status: Literal["pending", "approved", "rejected", "all"] = Query(
        default="pending", description="Which proposals"
    ),
) -> ProposalListResponse:
    """The change proposals the caller may decide, newest first."""
    org_id, viewer = await request_viewer(request)
    rows = await reviewable(_db(request), org_id, viewer, None if status == "all" else status)
    return ProposalListResponse(proposals=[_response(p, with_texts=False) for p in rows])


@router.get("/proposals/{proposal_id}", response_model=ProposalResponse)
async def get_proposal(proposal_id: str, request: Request) -> ProposalResponse:
    """One proposal with its edits and both texts."""
    org_id, viewer = await request_viewer(request)
    try:
        found = await reviewable_one(_db(request), org_id, viewer, proposal_id)
    except ProposalError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    return _response(found, with_texts=True)


@router.post("/proposals/{proposal_id}/approve", response_model=ProposalResponse)
async def approve_proposal(
    proposal_id: str, body: ApproveRequest, request: Request
) -> ProposalResponse:
    """Save the proposed text (or the caller's edit of it) to the agent or skill. A skill that
    follows an external source is not saved: a pull request is opened on its repository with
    the caller's linked GitHub account (``change_request_url``)."""
    org_id, viewer = await request_viewer(request)
    db_mgr = _db(request)
    try:
        decided = await approve(
            db_mgr,
            org_id,
            viewer,
            proposal_id,
            content=body.content,
            note=body.note,
            audit=AuditService(db_mgr),
            state=request.app.state,
        )
    except ProposalError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    return _response(decided, with_texts=True)


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalResponse)
async def reject_proposal(
    proposal_id: str, body: RejectRequest, request: Request
) -> ProposalResponse:
    """Refuse the proposal; the agent sees the note with ``list_my_proposals``."""
    org_id, viewer = await request_viewer(request)
    db_mgr = _db(request)
    try:
        decided = await reject(
            db_mgr,
            org_id,
            viewer,
            proposal_id,
            note=body.note,
            audit=AuditService(db_mgr),
        )
    except ProposalError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    return _response(decided, with_texts=True)
