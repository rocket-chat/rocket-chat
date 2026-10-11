"""Session listing ergonomics: keyset pagination, title search, pin/archive.

The sidebar must stay usable with hundreds of sessions, so the list is a keyset page
rather than an unbounded dump. The response body stays a bare array (the shape the
cockpit already consumes) and the cursor for the next page travels in the
``X-Next-Cursor`` response header — a header keeps the change backward-compatible with
every existing ``GET /v1/sessions`` caller while still exposing the keyset position.

Mutations publish ``session_updated`` on the session topic so other open tabs converge
without a reload.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from api.auth.access import _check_session_access, _get_request_identity
from api.models import SessionResponse
from api.services.session_support import _to_response
from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent
from specifications.interfaces.session import (
    SessionCursor,
    SessionStoreProtocol,
    decode_session_cursor,
)

router = APIRouter(prefix="/v1", tags=["Sessions"])

NEXT_CURSOR_HEADER = "X-Next-Cursor"
MAX_PAGE_SIZE = 200


class SessionListItem(SessionResponse):
    """A session as shown in the sidebar, carrying its pin/archive and fork state."""

    is_pinned: bool = Field(default=False, description="Pinned to the top of the sidebar")
    is_archived: bool = Field(default=False, description="Hidden from the default listing")
    forked_from: dict[str, Any] | None = Field(
        default=None,
        description="Origin of a forked session: {session_id, turn}",
    )


class SessionFlagsRequest(BaseModel):
    """Payload to pin or archive one session."""

    is_pinned: bool | None = Field(default=None, description="New pinned state")
    is_archived: bool | None = Field(default=None, description="New archived state")


def _forked_from(record: Any) -> dict[str, Any] | None:
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    value = metadata.get("forked_from")
    return value if isinstance(value, dict) else None


def session_list_item(record: Any) -> SessionListItem:
    """Serialize a ``SessionRecord`` for the sidebar, reusing the shared session mapping."""
    base = _to_response(record)
    return SessionListItem(
        **base.model_dump(),
        is_pinned=bool(record.is_pinned),
        is_archived=bool(record.is_archived),
        forked_from=_forked_from(record),
    )


async def _publish_session_updated(request: Request, record: Any) -> None:
    """Broadcast a metadata mutation so every other subscriber of the session converges."""
    event_bus: EventBusProtocol | None = getattr(request.app.state, "event_bus", None)
    if event_bus is None:
        return
    await event_bus.publish(
        SessionEvent(
            session_id=record.session_id,
            event_type=EventType.SESSION_UPDATED,
            payload={
                "session_id": record.session_id,
                "title": record.title,
                "is_pinned": bool(record.is_pinned),
                "is_archived": bool(record.is_archived),
            },
        )
    )


@router.get("/sessions", response_model=list[SessionListItem])
async def list_sessions(
    request: Request,
    response: Response,
    tenant_org_id: str | None = Query(None),
    tenant_user_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(None, description="Opaque keyset cursor from X-Next-Cursor"),
    archived: bool = Query(False, description="Include archived sessions"),
    q: str | None = Query(None, description="Case-insensitive title search"),
) -> list[SessionListItem]:
    """List one keyset page of sessions, newest ``(updated_at, session_id)`` first."""
    store: SessionStoreProtocol = request.app.state.session_store
    req_org, req_user, roles = _get_request_identity(request)
    is_admin = "admin" in roles
    # A non-admin can never retarget the list to another org: the client-supplied
    # `tenant_org_id` is ignored and the authenticated identity's org is used. RLS also
    # scopes rows, but the explicit predicate must not silently blank the sidebar when a
    # stale client sends a wrong org.
    effective_org = (tenant_org_id or req_org) if is_admin else req_org

    parsed_cursor: SessionCursor | None = None
    if cursor:
        try:
            parsed_cursor = decode_session_cursor(cursor)
        except ValueError as err:
            # A bad cursor is a client error: falling back to page one would make a
            # paging loop repeat the first page forever.
            raise HTTPException(status_code=400, detail=str(err)) from err

    page = await store.list_sessions_page(
        tenant_org_id=effective_org,
        tenant_user_id=tenant_user_id if is_admin else None,
        visible_to_user_id=None if is_admin else req_user,
        include_archived=archived,
        query=q,
        limit=limit,
        cursor=parsed_cursor,
    )
    if page.next_cursor:
        response.headers[NEXT_CURSOR_HEADER] = page.next_cursor
    return [session_list_item(record) for record in page.records]


@router.patch("/sessions/{session_id}/flags", response_model=SessionListItem)
async def update_session_flags(
    session_id: str,
    req: SessionFlagsRequest,
    request: Request,
) -> SessionListItem:
    """Pin or archive one session and broadcast the change to other tabs."""
    store: SessionStoreProtocol = request.app.state.session_store
    _, user_id, roles = _get_request_identity(request)
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, user_id, roles, require_write=True)

    updated = await store.set_session_flags(
        session_id,
        record.tenant_org_id,
        is_pinned=req.is_pinned,
        is_archived=req.is_archived,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await _publish_session_updated(request, updated)
    return session_list_item(updated)
