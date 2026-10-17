"""Subagent delegation transcripts and execution history (Item D2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _check_session_access, _get_request_identity
from api.services.subagent_transcripts import get_subagent_transcript_store
from specifications.interfaces.agent import SubagentTranscriptRecord
from specifications.interfaces.session import SessionStoreProtocol

router = APIRouter(prefix="/v1", tags=["Subagents"])


class SubagentTranscriptSummary(BaseModel):
    id: str = Field(..., description="Unique transcript record identifier")
    session_id: str = Field(..., description="Parent session identifier")
    subagent_id: str = Field(..., description="Subagent delegation identifier")
    role: str = Field(..., description="Role title or role key of the subagent")
    task: str = Field(..., description="Task objective delegated to the subagent")
    summary: str = Field(..., description="Final synthesized report summary")
    status: str = Field(..., description="Outcome status (completed, partial, failed, etc.)")
    turns_taken: int = Field(..., description="Number of execution turns consumed")
    tool_calls_count: int = Field(..., description="Number of tool calls executed")
    duration_ms: int = Field(..., description="Total elapsed execution time in milliseconds")
    tokens: int = Field(..., description="Total prompt and completion tokens consumed")
    cost_usd: float = Field(..., description="Estimated cost in USD")
    changed_files: list[str] = Field(
        default_factory=list, description="Files modified by this delegate"
    )
    artefacts: list[str] = Field(
        default_factory=list, description="Artefacts produced by this delegate"
    )
    created_at: int = Field(..., description="Epoch timestamp in seconds")


class SubagentTranscriptDetail(SubagentTranscriptSummary):
    messages: list[dict[str, Any]] = Field(
        default_factory=list, description="Full conversation transcript of the subagent execution"
    )


def _to_summary(r: SubagentTranscriptRecord) -> SubagentTranscriptSummary:
    return SubagentTranscriptSummary(
        id=r.id,
        session_id=r.session_id,
        subagent_id=r.subagent_id,
        role=r.role,
        task=r.task,
        summary=r.summary,
        status=r.status,
        turns_taken=r.turns_taken,
        tool_calls_count=r.tool_calls_count,
        duration_ms=r.duration_ms,
        tokens=r.tokens,
        cost_usd=r.cost_usd,
        changed_files=r.changed_files,
        artefacts=r.artefacts,
        created_at=r.created_at,
    )


def _to_detail(r: SubagentTranscriptRecord) -> SubagentTranscriptDetail:
    return SubagentTranscriptDetail(
        id=r.id,
        session_id=r.session_id,
        subagent_id=r.subagent_id,
        role=r.role,
        task=r.task,
        summary=r.summary,
        status=r.status,
        turns_taken=r.turns_taken,
        tool_calls_count=r.tool_calls_count,
        duration_ms=r.duration_ms,
        tokens=r.tokens,
        cost_usd=r.cost_usd,
        changed_files=r.changed_files,
        artefacts=r.artefacts,
        messages=r.messages,
        created_at=r.created_at,
    )


@router.get(
    "/sessions/{session_id}/subagents",
    response_model=list[SubagentTranscriptSummary],
    summary="List delegated subagent executions for a session",
)
async def list_session_subagents(
    session_id: str, request: Request
) -> list[SubagentTranscriptSummary]:
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _, user_id, roles = _get_request_identity(request)
    _check_session_access(record, user_id, roles)

    db_mgr = getattr(request.app.state, "db_manager", None)
    transcript_store = get_subagent_transcript_store(db_mgr)
    rows = await transcript_store.list_transcripts(session_id, record.tenant_org_id)
    return [_to_summary(r) for r in rows]


@router.get(
    "/sessions/{session_id}/subagents/{subagent_id}",
    response_model=SubagentTranscriptDetail,
    summary="Get detailed transcript and messages for a delegated subagent",
)
async def get_session_subagent_transcript(
    session_id: str, subagent_id: str, request: Request
) -> SubagentTranscriptDetail:
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _, user_id, roles = _get_request_identity(request)
    _check_session_access(record, user_id, roles)

    db_mgr = getattr(request.app.state, "db_manager", None)
    transcript_store = get_subagent_transcript_store(db_mgr)
    subagent_record = await transcript_store.get_transcript(
        session_id, subagent_id, record.tenant_org_id
    )
    if subagent_record is None:
        raise HTTPException(status_code=404, detail="Subagent transcript not found")
    return _to_detail(subagent_record)
