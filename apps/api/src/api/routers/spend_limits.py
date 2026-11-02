"""Spend limits: the organization's table of them, and what is left for a session.

Reading the table is for administrators; changing it needs the ``manage_spend_limits``
capability (administrators by default). A team's administrators add, change and remove the
limits their team owns, on the team and its agents only; every limit applies at once, so theirs
can only tighten the organization's. Every change is audited.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from api.auth.access import _check_session_access, _get_request_identity, _require_admin
from api.auth.capabilities import Capability, require
from api.services.audit import AuditService
from api.services.budgets import BudgetService, LimitStatus
from api.services.ownership import request_viewer, team_of
from api.services.personas import agent_owner, get_persona
from api.services.spend_limits import Mode, Period, SpendLimit, SubjectKind, describe
from api.services.teams import get_team
from specifications.interfaces.llm import ModelScope
from specifications.interfaces.ownership import Owner, Viewer, can_manage

router = APIRouter(prefix="/v1", tags=["Spend limits"])


class SpendLimitView(BaseModel):
    id: str
    subject_kind: str
    subject_id: str | None
    member_id: str | None
    period: str
    amount_usd: float
    mode: str
    warn_at_percent: int
    is_ceiling: bool
    label: str
    created_by: str
    created_at: int
    updated_at: int
    # The team that owns the limit; ``None``: the organization's.
    team_id: str | None = None
    # What has been spent in the current window when the limit has one counter (a default
    # for each member, a session or a turn limit has one per subject: not shown here).
    spent_usd: float | None = None
    resets_at: int | None = None


class SpendLimitRequest(BaseModel):
    subject_kind: SubjectKind
    subject_id: str | None = Field(default=None, max_length=128)
    member_id: str | None = Field(default=None, max_length=64)
    period: Period
    amount_usd: float = Field(..., gt=0, le=1_000_000)
    mode: Mode = "block"
    warn_at_percent: int = Field(default=80, ge=1, le=100)
    is_ceiling: bool = False

    @model_validator(mode="after")
    def _subject_matches(self) -> SpendLimitRequest:
        needs_subject = self.subject_kind in ("team", "agent", "agent_member", "slack_workspace")
        if needs_subject and not self.subject_id:
            raise ValueError(f"a {self.subject_kind} limit names its subject (subject_id)")
        if self.subject_kind == "org" and (self.subject_id or self.member_id):
            raise ValueError("an organization limit has no subject")
        if self.member_id and self.subject_kind != "agent_member":
            raise ValueError("member_id is for an agent limit of one member")
        default_for_each = (self.subject_kind == "member" and not self.subject_id) or (
            self.subject_kind == "agent_member" and not self.member_id
        )
        if self.is_ceiling and not default_for_each:
            raise ValueError("only a default for each member can be a ceiling")
        return self


class SpendLimitUpdate(BaseModel):
    amount_usd: float | None = Field(default=None, gt=0, le=1_000_000)
    mode: Mode | None = None
    warn_at_percent: int | None = Field(default=None, ge=1, le=100)
    is_ceiling: bool | None = None


class SessionSpend(BaseModel):
    """The limits that apply to the caller in this session, tightest first."""

    limits: list[dict[str, Any]]
    tightest: dict[str, Any] | None = None


def _service(request: Request) -> BudgetService:
    service = getattr(request.app.state, "budget_service", None)
    if not isinstance(service, BudgetService):
        raise HTTPException(status_code=503, detail="Spend limits are not available")
    return service


def _view(limit: SpendLimit, status: LimitStatus | None = None) -> SpendLimitView:
    return SpendLimitView(
        id=limit.id,
        subject_kind=limit.subject_kind,
        subject_id=limit.subject_id,
        member_id=limit.member_id,
        period=limit.period,
        amount_usd=limit.amount_usd,
        mode=limit.mode,
        warn_at_percent=limit.warn_at_percent,
        is_ceiling=limit.is_ceiling,
        team_id=limit.team_id,
        label=describe(limit),
        created_by=limit.created_by,
        created_at=limit.created_at,
        updated_at=limit.updated_at,
        spent_usd=round(status.spent_usd, 4) if status else None,
        resets_at=status.resets_at if status else None,
    )


def _single_counter_scope(limit: SpendLimit) -> ModelScope | None:
    """A scope whose counter is the limit's only one, to show what it has used."""
    if limit.period in ("session", "turn"):
        return None
    if limit.subject_kind == "org":
        return ModelScope(org_id=limit.org_id, user_id="")
    if limit.subject_kind == "team" and limit.subject_id:
        return ModelScope(org_id=limit.org_id, user_id="", team_id=limit.subject_id)
    if limit.subject_kind == "member" and limit.subject_id:
        return ModelScope(org_id=limit.org_id, user_id=limit.subject_id)
    if limit.subject_kind == "agent" and limit.subject_id:
        return ModelScope(org_id=limit.org_id, user_id="", agent_id=limit.subject_id)
    if limit.subject_kind == "agent_member" and limit.subject_id and limit.member_id:
        return ModelScope(org_id=limit.org_id, user_id=limit.member_id, agent_id=limit.subject_id)
    if limit.subject_kind == "slack_workspace" and limit.subject_id:
        return ModelScope(org_id=limit.org_id, user_id=f"slack:{limit.subject_id}")
    return None


async def _usage(service: BudgetService, limit: SpendLimit) -> LimitStatus | None:
    scope = _single_counter_scope(limit)
    if scope is None:
        return None
    status = await service.status(scope)
    return next((item for item in status.limits if item.limit.id == limit.id), None)


async def _audit(
    request: Request, org_id: str, actor: str, summary: str, limit: SpendLimit
) -> None:
    await AuditService(getattr(request.app.state, "db_manager", None)).record(
        "settings_change",
        org_id=org_id,
        actor_user_id=actor,
        result_summary=summary,
        metadata={
            "limit_id": limit.id,
            "subject": limit.subject_kind,
            "subject_id": limit.subject_id,
            "period": limit.period,
            "amount_usd": limit.amount_usd,
            "mode": limit.mode,
        },
    )


@router.get("/admin/spend-limits", response_model=list[SpendLimitView])
async def list_spend_limits(request: Request) -> list[SpendLimitView]:
    """The organization's spend limits, with what each has used in its current window."""
    org_id, _, _ = _require_admin(request)
    service = _service(request)
    return [
        _view(limit, await _usage(service, limit)) for limit in await service.store.list(org_id)
    ]


@router.post("/admin/spend-limits", response_model=SpendLimitView)
async def create_spend_limit(body: SpendLimitRequest, request: Request) -> SpendLimitView:
    """Add a spend limit (needs ``manage_spend_limits``)."""
    org_id, actor, _ = await require(request, Capability.MANAGE_SPEND_LIMITS)
    db_mgr = getattr(request.app.state, "db_manager", None)
    if body.subject_kind == "team" and await get_team(db_mgr, org_id, str(body.subject_id)) is None:
        raise HTTPException(status_code=422, detail="That team does not exist.")
    created = await _create(request, org_id, actor, body, team_id=None)
    return _view(created)


async def _create(
    request: Request, org_id: str, actor: str, body: SpendLimitRequest, *, team_id: str | None
) -> SpendLimit:
    service = _service(request)
    now = int(time.time())
    created = await service.store.create(
        SpendLimit(
            id="",
            org_id=org_id,
            subject_kind=body.subject_kind,
            subject_id=body.subject_id or None,
            member_id=body.member_id or None,
            period=body.period,
            amount_usd=body.amount_usd,
            mode=body.mode,
            warn_at_percent=body.warn_at_percent,
            is_ceiling=body.is_ceiling,
            created_by=actor,
            created_at=now,
            updated_at=now,
            team_id=team_id,
        )
    )
    service.forget(org_id)
    await _audit(request, org_id, actor, f"added a spend limit: {describe(created)}", created)
    return created


@router.patch("/admin/spend-limits/{limit_id}", response_model=SpendLimitView)
async def update_spend_limit(
    limit_id: str, body: SpendLimitUpdate, request: Request
) -> SpendLimitView:
    org_id, actor, _ = await require(request, Capability.MANAGE_SPEND_LIMITS)
    service = _service(request)
    changes = body.model_dump(exclude_none=True)
    current = await service.store.get(org_id, limit_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Spend limit not found")
    if changes.get("is_ceiling") and not current.is_default_for_each:
        raise HTTPException(
            status_code=422, detail="only a default for each member can be a ceiling"
        )
    updated = await service.store.update(org_id, limit_id, **changes)
    if updated is None:
        raise HTTPException(status_code=404, detail="Spend limit not found")
    service.forget(org_id)
    await _audit(request, org_id, actor, f"changed a spend limit: {describe(updated)}", updated)
    return _view(updated)


@router.delete("/admin/spend-limits/{limit_id}")
async def delete_spend_limit(limit_id: str, request: Request) -> dict[str, bool]:
    org_id, actor, _ = await require(request, Capability.MANAGE_SPEND_LIMITS)
    service = _service(request)
    removed = await service.store.delete(org_id, limit_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="Spend limit not found")
    service.forget(org_id)
    await _audit(request, org_id, actor, f"removed a spend limit: {describe(removed)}", removed)
    return {"deleted": True}


@router.get("/sessions/{session_id}/spend", response_model=SessionSpend)
async def session_spend(
    session_id: str, request: Request, agent_id: str | None = None
) -> SessionSpend:
    """What the caller has left under the limits of this session (the header chip)."""
    org_id, user_id, roles = _get_request_identity(request)
    record = await request.app.state.session_store.get_session(session_id)
    if record is None or record.tenant_org_id != org_id:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, user_id, roles)
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    service = _service(request)
    chosen = agent_id or metadata.get("agent_id") or None
    agent = await get_persona(
        getattr(request.app.state, "db_manager", None),
        org_id,
        user_id,
        chosen,
        enforce_access=False,
    )
    status = await service.status(
        ModelScope(
            org_id=org_id,
            user_id=record.tenant_user_id,
            session_id=session_id,
            requester_user_id=user_id,
            agent_id=chosen,
            team_id=team_of(agent),
        )
    )
    ordered = sorted(status.limits, key=lambda s: (s.limit.mode != "block", s.remaining_usd))
    tightest = status.tightest()
    return SessionSpend(
        limits=[{**s.payload(), "remaining": round(s.remaining_usd, 4)} for s in ordered],
        tightest={**tightest.payload(), "remaining": round(tightest.remaining_usd, 4)}
        if tightest
        else None,
    )


# -- a team's own limits --------------------------------------------------------------------


async def _team_manager(request: Request, team_id: str) -> tuple[str, Viewer]:
    org_id, viewer = await request_viewer(request)
    if await get_team(getattr(request.app.state, "db_manager", None), org_id, team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found.")
    if not can_manage(viewer, Owner("team", team_id=team_id)):
        raise HTTPException(status_code=403, detail="Only the team's administrators can do this.")
    return org_id, viewer


async def _about_the_team(request: Request, org_id: str, team_id: str, limit: SpendLimit) -> bool:
    """Whether ``limit`` counts the team's spend: on the team, or on one of its agents."""
    if limit.subject_kind == "team":
        return limit.subject_id == team_id
    if limit.subject_kind not in ("agent", "agent_member") or not limit.subject_id:
        return False
    owner = await agent_owner(
        getattr(request.app.state, "db_manager", None), org_id, limit.subject_id
    )
    return owner is not None and owner.team_id == team_id


async def _team_limit(request: Request, org_id: str, team_id: str, limit_id: str) -> SpendLimit:
    found = await _service(request).store.get(org_id, limit_id)
    if found is None or found.team_id != team_id:
        raise HTTPException(status_code=404, detail="Spend limit not found")
    return found


@router.get("/teams/{team_id}/spend-limits", response_model=list[SpendLimitView])
async def list_team_spend_limits(team_id: str, request: Request) -> list[SpendLimitView]:
    """The limits that count the team's spend (the organization's and its own), with what
    each has used (the team's administrators)."""
    org_id, _ = await _team_manager(request, team_id)
    service = _service(request)
    return [
        _view(limit, await _usage(service, limit))
        for limit in await service.store.list(org_id)
        if await _about_the_team(request, org_id, team_id, limit)
    ]


@router.post("/teams/{team_id}/spend-limits", response_model=SpendLimitView)
async def create_team_spend_limit(
    team_id: str, body: SpendLimitRequest, request: Request
) -> SpendLimitView:
    """Add a limit the team owns, on the team or one of its agents."""
    org_id, viewer = await _team_manager(request, team_id)
    if body.is_ceiling:
        raise HTTPException(status_code=422, detail="A team's limit cannot be a ceiling.")
    draft = SpendLimit(
        id="",
        org_id=org_id,
        subject_kind=body.subject_kind,
        subject_id=body.subject_id or None,
        member_id=body.member_id or None,
        period=body.period,
        amount_usd=body.amount_usd,
        mode=body.mode,
        warn_at_percent=body.warn_at_percent,
        is_ceiling=False,
        created_by=viewer.user_id,
        created_at=0,
        updated_at=0,
    )
    if not await _about_the_team(request, org_id, team_id, draft):
        raise HTTPException(
            status_code=422, detail="A team's limit is on the team or one of its agents."
        )
    return _view(await _create(request, org_id, viewer.user_id, body, team_id=team_id))


@router.patch("/teams/{team_id}/spend-limits/{limit_id}", response_model=SpendLimitView)
async def update_team_spend_limit(
    team_id: str, limit_id: str, body: SpendLimitUpdate, request: Request
) -> SpendLimitView:
    org_id, viewer = await _team_manager(request, team_id)
    await _team_limit(request, org_id, team_id, limit_id)
    if body.is_ceiling:
        raise HTTPException(status_code=422, detail="A team's limit cannot be a ceiling.")
    service = _service(request)
    updated = await service.store.update(org_id, limit_id, **body.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="Spend limit not found")
    service.forget(org_id)
    await _audit(
        request, org_id, viewer.user_id, f"changed a team spend limit: {describe(updated)}", updated
    )
    return _view(updated)


@router.delete("/teams/{team_id}/spend-limits/{limit_id}")
async def delete_team_spend_limit(team_id: str, limit_id: str, request: Request) -> dict[str, bool]:
    org_id, viewer = await _team_manager(request, team_id)
    await _team_limit(request, org_id, team_id, limit_id)
    service = _service(request)
    removed = await service.store.delete(org_id, limit_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="Spend limit not found")
    service.forget(org_id)
    await _audit(
        request, org_id, viewer.user_id, f"removed a team spend limit: {describe(removed)}", removed
    )
    return {"deleted": True}
