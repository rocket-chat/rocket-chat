"""Remembered approvals: the ones a session carries, the ones a member keeps per repository.

Rules are created only by answering an approval (``remember`` on the approval frame or
``POST /v1/sessions/{id}/approvals/{action_id}``); these routes list and revoke them.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _check_session_access, _get_request_identity
from api.services.approval_rules import (
    ApprovalRuleStore,
    StoredRule,
    allowed_scopes,
    publish_rules_changed,
)
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol

router = APIRouter(prefix="/v1", tags=["Approvals"])


class ApprovalRuleView(BaseModel):
    id: str
    scope: str = Field(..., description="session or repository")
    tool: str = Field(..., description="The tool the rule allows")
    command: str = Field("", description="The exact command (empty: every call of the tool)")
    repository: str = Field("", description="owner/name, for a repository rule")
    session_id: str | None = None
    created_by: str = Field(..., description="Who saved it")
    created_at: int
    last_used_at: int | None = None


class ApprovalRulesView(BaseModel):
    allowed_scopes: list[str] = Field(
        ..., description="Scopes the organization lets members remember approvals in"
    )
    rules: list[ApprovalRuleView]


def _view(rule: StoredRule) -> ApprovalRuleView:
    return ApprovalRuleView(
        id=rule.id,
        scope=rule.scope,
        tool=rule.tool,
        command=rule.command,
        repository=rule.repository,
        session_id=rule.session_id,
        created_by=rule.user_id,
        created_at=rule.created_at,
        last_used_at=rule.last_used_at,
    )


async def _session(request: Request, session_id: str, *, write: bool) -> SessionRecord:
    org_id, user_id, roles = _get_request_identity(request)
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id, tenant_org_id=org_id, include_history=False)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, user_id, roles, require_write=write)
    return record


@router.get("/sessions/{session_id}/approval-rules", response_model=ApprovalRulesView)
async def list_session_rules(session_id: str, request: Request) -> ApprovalRulesView:
    """What this session no longer asks about ("Allow for this session"), and the scopes the
    organization allows."""
    record = await _session(request, session_id, write=False)
    db = getattr(request.app.state, "db_manager", None)
    rules = await ApprovalRuleStore(db).for_session(record.tenant_org_id, session_id)
    return ApprovalRulesView(
        allowed_scopes=list(await allowed_scopes(db, record.tenant_org_id)),
        rules=[_view(r) for r in rules],
    )


@router.delete("/sessions/{session_id}/approval-rules/{rule_id}", response_model=ApprovalRuleView)
async def revoke_session_rule(session_id: str, rule_id: str, request: Request) -> ApprovalRuleView:
    """Ask again: the session's next call of the tool raises the approval it used to."""
    record = await _session(request, session_id, write=True)
    store = ApprovalRuleStore(getattr(request.app.state, "db_manager", None))
    rule = await store.get(record.tenant_org_id, rule_id)
    if rule is None or rule.session_id != session_id:
        raise HTTPException(status_code=404, detail="No such rule in this session")
    await store.revoke(record.tenant_org_id, rule_id)
    await publish_rules_changed(
        getattr(request.app.state, "event_bus", None), session_id, "revoked", rule
    )
    return _view(rule)


@router.get("/approval-rules", response_model=ApprovalRulesView)
async def list_my_repository_rules(request: Request) -> ApprovalRulesView:
    """Your "always for this command in this repository" rules, in every session."""
    org_id, user_id, _ = _get_request_identity(request)
    db = getattr(request.app.state, "db_manager", None)
    rules = await ApprovalRuleStore(db).for_member(org_id, user_id)
    return ApprovalRulesView(
        allowed_scopes=list(await allowed_scopes(db, org_id)),
        rules=[_view(r) for r in rules],
    )


@router.delete("/approval-rules/{rule_id}", response_model=ApprovalRuleView)
async def revoke_my_repository_rule(rule_id: str, request: Request) -> ApprovalRuleView:
    """Revoke one of your repository rules; it stops applying at the next gate of any turn."""
    org_id, user_id, _ = _get_request_identity(request)
    store = ApprovalRuleStore(getattr(request.app.state, "db_manager", None))
    rule = await store.get(org_id, rule_id)
    if rule is None or rule.user_id != user_id or rule.scope != "repository":
        raise HTTPException(status_code=404, detail="No such rule")
    await store.revoke(org_id, rule_id)
    return _view(rule)
