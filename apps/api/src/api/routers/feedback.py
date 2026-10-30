"""Answer feedback: a member rates an answer; administrators export the ratings."""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _require_admin
from api.auth.session_lookup import session_for_request
from api.services.answer_feedback import FeedbackError, FeedbackStore

router = APIRouter(prefix="/v1", tags=["Feedback"])


class FeedbackRequest(BaseModel):
    turn_number: int = Field(..., ge=1, description="The answered turn, counted from 1")
    rating: Literal["up", "down"]
    comment: str = Field(default="", max_length=4_000)


class FeedbackView(BaseModel):
    session_id: str
    user_id: str
    turn_number: int
    rating: str
    comment: str
    prompt_excerpt: str
    answer_excerpt: str
    model: str | None
    agent_id: str | None
    updated_at: int


def _store(request: Request) -> FeedbackStore:
    return FeedbackStore(getattr(request.app.state, "db_manager", None))


@router.put("/sessions/{session_id}/feedback", response_model=FeedbackView)
async def rate_answer(session_id: str, body: FeedbackRequest, request: Request) -> FeedbackView:
    """Rate one answer of a session the caller can see (again to change the rating)."""
    caller = await session_for_request(request, session_id, require_write=False)
    try:
        saved = await _store(request).rate(
            caller.record, caller.user_id, body.turn_number, body.rating, body.comment
        )
    except FeedbackError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return FeedbackView(**asdict(saved))


@router.get("/sessions/{session_id}/feedback", response_model=list[FeedbackView])
async def my_feedback(session_id: str, request: Request) -> list[FeedbackView]:
    """The caller's own ratings on the session."""
    caller = await session_for_request(request, session_id, require_write=False)
    found = await _store(request).mine(caller.org_id, session_id, caller.user_id)
    return [FeedbackView(**asdict(f)) for f in found]


@router.get("/admin/feedback", response_model=list[FeedbackView])
async def export_feedback(request: Request, since: int = 0) -> list[FeedbackView]:
    """Every rating of the organization since ``since`` (epoch seconds), newest first, with
    the prompt and answer excerpts (administrators)."""
    org_id, _, _ = _require_admin(request)
    return [FeedbackView(**asdict(f)) for f in await _store(request).export(org_id, since)]
