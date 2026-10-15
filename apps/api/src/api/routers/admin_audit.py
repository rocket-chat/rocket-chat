"""Admin audit-trail API.

Exposes the durable governance audit log to organisation administrators, newest first,
with keyset pagination. The retention policy that governs how long rows live is returned
alongside the page so an admin can see the window in force without a second call.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.auth.access import _require_admin as require_admin
from api.db.session import DatabaseSessionManager
from api.services.audit import (
    AuditEvent,
    AuditService,
    GovernancePolicy,
    decode_audit_cursor,
    encode_audit_cursor,
)
from api.services.settings import load_org_config

router = APIRouter(prefix="/v1", tags=["Governance & Audit"])


class AuditEventResponse(BaseModel):
    """One durable audit row: who did what, when, and with what result."""

    id: str = Field(..., description="Audit event identifier")
    event_type: str = Field(
        ...,
        description="tool_call | approval_request | approval_decision | model_call | "
        "credential_change | settings_change | git_publish",
    )
    actor_user_id: str | None = Field(default=None, description="User who caused the event")
    session_id: str | None = Field(default=None, description="Session the event belongs to")
    tool_name: str | None = Field(default=None, description="Tool, action or model involved")
    risk_level: str | None = Field(default=None, description="Risk classification when known")
    approved_by: str | None = Field(
        default=None, description="Approver attribution for an approval decision"
    )
    result_summary: str | None = Field(default=None, description="Short outcome summary")
    created_at: int = Field(..., description="Event time, epoch seconds")
    redacted: bool = Field(..., description="Whether redaction was applied to this event")
    redaction_count: int = Field(..., description="Number of spans redacted")


class RetentionWindows(BaseModel):
    """The per-org retention policy in force, in days (null means kept indefinitely)."""

    session_days: int | None = Field(default=None, description="Session retention window")
    trace_days: int | None = Field(default=None, description="Turn-trace retention window")
    memory_days: int | None = Field(default=None, description="Memory retention window")
    attachment_days: int | None = Field(default=None, description="Attachment retention window")
    audit_days: int | None = Field(default=None, description="Audit-row retention window")


class AuditPage(BaseModel):
    """One page of the audit log plus the retention policy governing it."""

    events: list[AuditEventResponse] = Field(default_factory=list, description="Audit rows")
    next_cursor: str | None = Field(
        default=None, description="Pass as ?cursor= to fetch the next, older page"
    )
    retention: RetentionWindows = Field(..., description="Retention windows in force")


def _to_response(event: AuditEvent) -> AuditEventResponse:
    return AuditEventResponse(
        id=event.id,
        event_type=event.event_type,
        actor_user_id=event.actor_user_id,
        session_id=event.session_id,
        tool_name=event.tool_name,
        risk_level=event.risk_level,
        approved_by=event.approved_by,
        result_summary=event.result_summary,
        created_at=event.created_at,
        redacted=event.redacted,
        redaction_count=event.redaction_count,
    )


def _require_admin(request: Request) -> tuple[str, str, list[str]]:
    return require_admin(request)


@router.get("/admin/audit", response_model=AuditPage)
async def list_audit_events(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200, description="Page size"),
    cursor: str | None = Query(default=None, description="Opaque cursor from a previous page"),
) -> AuditPage:
    """List the caller organisation's audit trail, newest first (admin only)."""
    org_id, _user_id, _roles = _require_admin(request)

    db_manager: DatabaseSessionManager | None = getattr(request.app.state, "db_manager", None)
    if db_manager is None:
        # The audit trail is a durable record; without PostgreSQL there is nothing to
        # audit against, and an empty 200 would falsely read as "no events".
        raise HTTPException(
            status_code=503,
            detail="The audit trail requires the PostgreSQL-backed store",
        )

    parsed_cursor: tuple[int, str] | None = None
    if cursor:
        try:
            parsed_cursor = decode_audit_cursor(cursor)
        except ValueError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err

    service = AuditService(db_manager)
    events = await service.list_page(org_id, limit=limit, cursor=parsed_cursor)
    next_cursor = encode_audit_cursor(events[-1]) if len(events) == limit else None

    governance_settings, _ = await load_org_config(db_manager, org_id, "governance")
    retention = GovernancePolicy.from_settings(governance_settings).retention
    return AuditPage(
        events=[_to_response(event) for event in events],
        next_cursor=next_cursor,
        retention=RetentionWindows(
            session_days=retention.session_days,
            trace_days=retention.trace_days,
            memory_days=retention.memory_days,
            attachment_days=retention.attachment_days,
            audit_days=retention.audit_days,
        ),
    )
