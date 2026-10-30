"""Sharing a session: with named members, with the whole organization, or by a public link.

Owners and admins manage sharing. A public link is a signed, expiring, revocable, read-only
capability (``/shared/<token>`` in the cockpit); an administrator can switch the feature off
for the organization with ``general.public_session_links``, which also disables links already
issued.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from api.auth import share_token
from api.auth.access import _get_request_identity
from api.auth.roles import is_admin
from api.db.session import current_tenant_org_id
from api.services.artifacts import get_artifact_store
from api.services.audit import AuditService
from api.services.redaction import RedactionPolicy, Redactor
from api.services.settings import effective_settings
from api.services.sharing import (
    PUBLIC_LINK_KEY,
    Member,
    members_by_email,
    members_by_id,
    parse_emails,
    public_artifacts,
    public_link_of,
    public_transcript,
)
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol

router = APIRouter(prefix="/v1", tags=["Sharing"])


class MemberView(BaseModel):
    user_id: str
    email: str
    name: str


class PublicLinkView(BaseModel):
    url: str = Field(..., description="Relative cockpit URL; prefix it with the site address")
    expires_at: int


class SharingView(BaseModel):
    org_wide: bool = Field(..., description="Every member of the organization can open it")
    members: list[MemberView] = Field(default_factory=list)
    unknown_emails: list[str] = Field(
        default_factory=list, description="Addresses that are not members of the organization"
    )
    public_links_enabled: bool = Field(
        ..., description="The organization allows public links (general.public_session_links)"
    )
    public_link: PublicLinkView | None = None


class UpdateSharingRequest(BaseModel):
    org_wide: bool = False
    emails: list[str] = Field(default_factory=list, max_length=100)


class CreateLinkRequest(BaseModel):
    expires_in_days: int = Field(default=share_token.DEFAULT_LINK_DAYS, ge=1, le=30)


class SharedStep(BaseModel):
    name: str
    kind: str = Field(..., description="edit (a file change) or step")
    arguments: str = Field(..., description="Redacted and cut; never the step's output")


class SharedMessage(BaseModel):
    role: str
    content: str
    steps: list[SharedStep] = Field(
        default_factory=list, description="The tool steps behind an answer (redacted, bounded)"
    )
    steps_cut: int = Field(0, description="Earlier steps of that answer not shown")


class SharedArtifact(BaseModel):
    title: str
    filename: str
    artifact_type: str
    content: str


class SharedConversation(BaseModel):
    title: str
    updated_at: int
    expires_at: int
    messages: list[SharedMessage]
    artifacts: list[SharedArtifact] = Field(default_factory=list)


async def _owned_session(
    request: Request, session_id: str
) -> tuple[SessionStoreProtocol, SessionRecord, str, str]:
    org_id, user_id, roles = _get_request_identity(request)
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id, tenant_org_id=org_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not is_admin(roles) and record.tenant_user_id != user_id:
        raise HTTPException(
            status_code=403, detail="Forbidden: Only session owner or admin can configure sharing"
        )
    return store, record, org_id, user_id


async def _links_enabled(request: Request, org_id: str) -> bool:
    general = await effective_settings(
        getattr(request.app.state, "db_manager", None), org_id, "general"
    )
    return bool(general["public_session_links"])


async def _view(
    request: Request, record: SessionRecord, org_id: str, unknown: list[str] | None = None
) -> SharingView:
    members = await members_by_id(
        getattr(request.app.state, "db_manager", None), org_id, list(record.collaborators)
    )
    link = public_link_of(record)
    return SharingView(
        org_wide=record.is_shared,
        members=[MemberView(user_id=m.user_id, email=m.email, name=m.name) for m in members],
        unknown_emails=unknown or [],
        public_links_enabled=await _links_enabled(request, org_id),
        public_link=PublicLinkView(url=link["url"], expires_at=link["expires_at"])
        if link
        else None,
    )


async def _audit(request: Request, org_id: str, user_id: str, session_id: str, what: str) -> None:
    await AuditService(getattr(request.app.state, "db_manager", None)).record(
        "settings_change",
        org_id=org_id,
        actor_user_id=user_id,
        result_summary=f"session sharing: {what}",
        metadata={"domain": "session_sharing", "session_id": session_id, "action": what},
    )


@router.get("/sessions/{session_id}/sharing", response_model=SharingView)
async def get_sharing(session_id: str, request: Request) -> SharingView:
    _, record, org_id, _ = await _owned_session(request, session_id)
    return await _view(request, record, org_id)


@router.put("/sessions/{session_id}/sharing", response_model=SharingView)
async def update_sharing(
    session_id: str, body: UpdateSharingRequest, request: Request
) -> SharingView:
    """Share with the whole organization and/or exactly the members with these emails."""
    store, record, org_id, user_id = await _owned_session(request, session_id)
    emails = parse_emails(body.emails)
    found: list[Member] = await members_by_email(
        getattr(request.app.state, "db_manager", None), org_id, emails
    )
    known = {m.email.lower() for m in found}
    collaborators = [m.user_id for m in found if m.user_id != record.tenant_user_id]

    def apply(target: SessionRecord) -> None:
        target.is_shared = body.org_wide
        target.collaborators = collaborators
        target.metadata = {
            **target.metadata,
            "is_shared": body.org_wide,
            "collaborators": collaborators,
        }

    updated = await store.mutate_session(session_id, org_id, apply) or record
    await _audit(request, org_id, user_id, session_id, "members updated")
    return await _view(request, updated, org_id, [e for e in emails if e not in known])


@router.post("/sessions/{session_id}/sharing/link", response_model=SharingView)
async def create_public_link(
    session_id: str, body: CreateLinkRequest, request: Request
) -> SharingView:
    """Create (or replace) the read-only public link; the previous link stops working."""
    store, record, org_id, user_id = await _owned_session(request, session_id)
    if not await _links_enabled(request, org_id):
        raise HTTPException(
            status_code=403, detail="Public session links are turned off for this organization"
        )
    link_id = secrets.token_urlsafe(8)
    token, expires_at = share_token.mint(session_id, org_id, link_id, body.expires_in_days)
    link: dict[str, Any] = {"id": link_id, "url": f"/shared/{token}", "expires_at": expires_at}

    def apply(target: SessionRecord) -> None:
        target.metadata = {**target.metadata, PUBLIC_LINK_KEY: link}

    updated = await store.mutate_session(session_id, org_id, apply) or record
    await _audit(request, org_id, user_id, session_id, "public link created")
    return await _view(request, updated, org_id)


@router.delete("/sessions/{session_id}/sharing/link", response_model=SharingView)
async def revoke_public_link(session_id: str, request: Request) -> SharingView:
    store, record, org_id, user_id = await _owned_session(request, session_id)

    def apply(target: SessionRecord) -> None:
        target.metadata = {k: v for k, v in target.metadata.items() if k != PUBLIC_LINK_KEY}

    updated = await store.mutate_session(session_id, org_id, apply) or record
    await _audit(request, org_id, user_id, session_id, "public link revoked")
    return await _view(request, updated, org_id)


@router.get("/shared/{token}", response_model=SharedConversation)
async def read_shared_conversation(token: str, request: Request, response: Response) -> Any:
    """The conversation behind a public link: no sign-in, read-only, never cached."""
    try:
        grant = share_token.verify(token)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    store: SessionStoreProtocol = request.app.state.session_store
    tenant = current_tenant_org_id.set(grant.org_id)
    try:
        record = await store.get_session(grant.session_id, tenant_org_id=grant.org_id)
        enabled = await _links_enabled(request, grant.org_id)
    finally:
        current_tenant_org_id.reset(tenant)
    link = public_link_of(record) if record else None
    if record is None or not enabled or link is None or link["id"] != grant.link_id:
        raise HTTPException(status_code=404, detail="This link is invalid or has expired")
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow",
            "Referrer-Policy": "no-referrer",
        }
    )
    redact = await _redactor(request, grant.org_id)
    artifacts = await get_artifact_store(
        getattr(request.app.state, "db_manager", None)
    ).list_artifacts(grant.session_id, grant.org_id)
    return SharedConversation(
        title=record.title,
        updated_at=record.updated_at,
        expires_at=grant.expires_at,
        messages=[SharedMessage(**m) for m in public_transcript(record, redact)],
        artifacts=[SharedArtifact(**a) for a in public_artifacts(artifacts, redact)],
    )


async def _redactor(request: Request, org_id: str) -> Callable[[str], str]:
    """The organization's redaction rules, as applied to anything a public link shows."""
    tenant = current_tenant_org_id.set(org_id)
    try:
        governance = await effective_settings(
            getattr(request.app.state, "db_manager", None), org_id, "governance"
        )
    finally:
        current_tenant_org_id.reset(tenant)
    redactor = Redactor(RedactionPolicy.from_settings(governance))
    return lambda text: redactor.redact_text(text)[0]
