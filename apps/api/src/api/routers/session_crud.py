"""Create, read, update, share and delete sessions."""

import logging
import uuid
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.offload import forget_session_offloads
from agent_core.tools.registry import ToolRegistry
from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)
from git_engine.engine import GitEngine
from pydantic import BaseModel

from api.auth.access import _check_session_access, _get_request_identity, _require_admin
from api.auth.roles import can_write, is_admin
from api.models import (
    CreateSessionRequest,
    ResetSessionsResponse,
    SessionResponse,
    ShareSessionRequest,
    UpdateSessionRequest,
)
from api.services.agent_access import AgentAccessDenied, require_usable_agent
from api.services.sandbox_catalogue import SandboxCatalogue
from api.services.session_support import (
    _CLIENT_PATCHABLE_METADATA,
    _to_response,
    apply_sharing,
    slice_history_tail,
    user_turns_in,
)
from api.services.settings import (
    effective_settings,
)
from api.services.title import TITLE_SOURCE_AUTO, TITLE_SOURCE_USER
from api.turns.context import TurnSelection
from api.turns.tasks import _spawn_background_turn
from specifications.interfaces.agent import (
    PERMISSION_MODES,
)
from specifications.interfaces.events import EventBusProtocol
from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.sandbox import (
    SandboxDriverProtocol,
    SandboxStatus,
)
from specifications.interfaces.session import (
    SESSION_REASONING_EFFORT_KEY,
    SESSION_REASONING_EFFORTS,
    SessionRecord,
    SessionSource,
    SessionStoreProtocol,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])

RESET_BATCH = 200


async def _choose_sandbox(
    request: Request, org_id: str, req: CreateSessionRequest, metadata: dict[str, Any]
) -> str | None:
    """The image a new session starts from, validated against the organization's catalogue.

    Nothing chosen means the catalogue's default at provisioning time. Naming an image that
    the organization does not offer is refused here, at creation, not when the sandbox starts.
    """
    if not (req.sandbox_id or req.container_image):
        return None
    db_mgr = getattr(request.app.state, "db_manager", None)
    catalogue = SandboxCatalogue(db_mgr)
    if req.sandbox_id:
        entry = await catalogue.resolve(org_id, sandbox_id=req.sandbox_id)
        if entry is None:
            raise HTTPException(status_code=422, detail="That sandbox is not in the catalogue.")
        metadata["sandbox_id"] = entry.id
        return entry.image
    image = str(req.container_image)
    sandboxes = await effective_settings(db_mgr, org_id, "sandboxes")
    if not await catalogue.image_is_offered(
        org_id, image, [str(p) for p in sandboxes.get("allowed_images") or ()]
    ):
        raise HTTPException(
            status_code=422, detail="That image is not in the organization's sandbox catalogue."
        )
    if entry := await catalogue.resolve(org_id, image=image):
        metadata["sandbox_id"] = entry.id
    return image


@router.post("/sessions", response_model=SessionResponse)
async def create_session(req: CreateSessionRequest, request: Request) -> SessionResponse:
    """Create a new agent session with optional sandbox provisioning."""
    store: SessionStoreProtocol = request.app.state.session_store
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)

    req_org, req_user, roles = _get_request_identity(request)
    if not can_write(roles):
        raise HTTPException(status_code=403, detail="Forbidden: viewers cannot create sessions")

    # Tenancy comes from the authenticated identity only; body fields are never trusted.
    effective_org = req_org
    effective_user = req_user

    # Only the keys a client may set later by PATCH: the rest (toolsets, fork and Slack
    # provenance, title provenance) is written by the server and would otherwise let a caller
    # enable server-only capabilities on its own session.
    metadata = {k: v for k, v in req.metadata.items() if k in _CLIENT_PATCHABLE_METADATA}
    _check_reasoning_effort(metadata)
    if req.permission_mode is not None:
        if req.permission_mode not in PERMISSION_MODES:
            raise HTTPException(
                status_code=422,
                detail=f"permission_mode must be one of: {', '.join(PERMISSION_MODES)}",
            )
        metadata["permission_mode"] = req.permission_mode
    elif metadata.get("permission_mode") not in (None, *PERMISSION_MODES):
        raise HTTPException(
            status_code=422,
            detail=f"permission_mode must be one of: {', '.join(PERMISSION_MODES)}",
        )
    if req.agent_id:
        await _usable_agent(request, effective_org, req_user, roles, req.agent_id)
        metadata["agent_id"] = req.agent_id
    chosen_image = await _choose_sandbox(request, effective_org, req, metadata)
    # An explicit choice wins; otherwise the organization's privacy default applies.
    is_shared = req.is_shared
    if "is_shared" not in req.model_fields_set:
        general = await effective_settings(
            getattr(request.app.state, "db_manager", None), effective_org, "general"
        )
        is_shared = general["session_privacy_default"] == "shared_org"
    metadata["is_shared"] = is_shared
    metadata["collaborators"] = req.collaborators

    session_id = f"session_{uuid.uuid4().hex[:10]}"

    title = req.title
    title_is_placeholder = (
        not title
        or title in ("New Session", "Mission Control Session")
        or title.startswith("Mission ")
    )
    if title_is_placeholder and req.initial_prompt:
        clean_prompt = " ".join(req.initial_prompt.strip().split())
        title = clean_prompt[:45].strip() + ("..." if len(clean_prompt) > 45 else "")
        # A prompt-derived title is a placeholder too: mark its provenance so the AI titler
        # replaces it on the first turn instead of treating it as a user-chosen title.
        metadata["title_source"] = TITLE_SOURCE_AUTO
    elif not title:
        title = "New Session"

    record = SessionRecord(
        session_id=session_id,
        tenant_org_id=effective_org,
        tenant_user_id=effective_user,
        title=title,
        source=SessionSource.WEB_UI,
        sandbox_status=SandboxStatus.NON_EXISTENT,
        container_image=chosen_image,
        is_shared=is_shared,
        collaborators=req.collaborators,
        metadata=metadata,
    )
    if req.initial_prompt:
        record.conversation_history.append(
            ChatMessage(role="user", content=req.initial_prompt, author_user_id=effective_user)
        )
    await store.create_session(record)

    if req.initial_prompt:
        orchestrator: AsyncReActOrchestrator = request.app.state.orchestrator
        event_bus: EventBusProtocol = request.app.state.event_bus
        registry: ToolRegistry | None = getattr(request.app.state, "tool_registry", None)
        git_engine: GitEngine | None = getattr(request.app.state, "git_engine", None)
        db_mgr = getattr(request.app.state, "db_manager", None)
        _spawn_background_turn(
            session_id=session_id,
            prompt=req.initial_prompt,
            orchestrator=orchestrator,
            event_bus=event_bus,
            driver=driver,
            registry=registry,
            store=store,
            model=req.model,
            git_engine=git_engine,
            db_mgr=db_mgr,
            concurrency_gate=getattr(request.app.state, "global_concurrency_gate", None),
            session_locks=getattr(request.app.state, "session_write_locks", None),
            selection=TurnSelection(agent_id=req.agent_id, raw=req.mode == "direct"),
        )

    return _to_response(record)


class SessionMessagesPage(BaseModel):
    """A page of conversation history messages with keyset position."""

    messages: list[dict[str, Any]]
    has_more: bool
    start_index: int
    total: int


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    request: Request,
    tail: int | None = Query(
        default=None,
        ge=1,
        le=500,
        description="Return only the newest N messages from conversation history",
    ),
) -> SessionResponse:
    """Retrieve session record and metadata."""
    store: SessionStoreProtocol = request.app.state.session_store
    req_org, req_user, roles = _get_request_identity(request)
    if hasattr(store, "get_session"):
        try:
            record = await store.get_session(session_id, tenant_org_id=req_org)
        except TypeError:
            record = await store.get_session(session_id)
    else:
        record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, req_user, roles, require_write=False)

    history = record.conversation_history or []
    total = len(history)
    if tail is not None:
        sliced, has_older = slice_history_tail(history, tail=tail)
        if has_older:
            return _to_response(
                record,
                history_override=sliced,
                has_older_history=True,
                total_messages=total,
                user_turns_before=user_turns_in(history[: total - len(sliced)]),
            )
    return _to_response(record, total_messages=total)


@router.get("/sessions/{session_id}/messages", response_model=SessionMessagesPage)
async def get_session_messages(
    session_id: str,
    request: Request,
    before_index: int | None = Query(
        default=None,
        description="Exclusive upper bound index in conversation history",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
        description="Number of earlier messages to return",
    ),
) -> SessionMessagesPage:
    """Fetch a page of earlier messages from a session's conversation history."""
    store: SessionStoreProtocol = request.app.state.session_store
    req_org, req_user, roles = _get_request_identity(request)
    if hasattr(store, "get_session"):
        try:
            record = await store.get_session(session_id, tenant_org_id=req_org)
        except TypeError:
            record = await store.get_session(session_id)
    else:
        record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, req_user, roles, require_write=False)

    history = record.conversation_history or []
    total = len(history)
    end = before_index if before_index is not None else total
    end = max(0, min(end, total))
    start = max(0, end - limit)
    slice_items = history[start:end]
    formatted = [
        {
            "role": m.role,
            "content": m.content or "",
            "reasoning": getattr(m, "reasoning", None),
            "tool_calls": getattr(m, "tool_calls", None),
            "author_user_id": getattr(m, "author_user_id", None),
            "agent_id": getattr(m, "agent_id", None),
            "agent_name": getattr(m, "agent_name", None),
            "model": getattr(m, "model", None),
            "created_at": getattr(m, "created_at", None),
        }
        for m in slice_items
    ]
    return SessionMessagesPage(
        messages=formatted,
        has_more=start > 0,
        start_index=start,
        total=total,
    )


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str, req: UpdateSessionRequest, request: Request
) -> SessionResponse:
    """Update an existing session's bound agent persona, title, sharing status, or metadata."""
    store: SessionStoreProtocol = request.app.state.session_store
    req_org, req_user, roles = _get_request_identity(request)
    if hasattr(store, "get_session"):
        try:
            record = await store.get_session(session_id, tenant_org_id=req_org)
        except TypeError:
            record = await store.get_session(session_id)
    else:
        record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, req_user, roles, require_write=True)

    _check_patch_rights(req, is_admin(roles) or record.tenant_user_id == req_user)
    await _usable_agent(request, record.tenant_org_id, req_user, roles, req.agent_id)
    # Applied to the stored record under its row lock: a turn may be recording its answer or
    # its active repository at the same moment, and a whole-record write would erase them.
    updated = await store.mutate_session(
        session_id, record.tenant_org_id, lambda current: _apply_patch(current, req)
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _to_response(updated)


def _check_reasoning_effort(metadata: dict[str, Any] | None) -> None:
    """422 for a reasoning effort a session cannot hold; null clears the session's choice."""
    effort = (metadata or {}).get(SESSION_REASONING_EFFORT_KEY)
    if effort is not None and effort not in SESSION_REASONING_EFFORTS:
        raise HTTPException(
            status_code=422,
            detail=f"reasoning_effort must be one of: {', '.join(SESSION_REASONING_EFFORTS)}",
        )


def _check_patch_rights(req: UpdateSessionRequest, is_owner_or_admin: bool) -> None:
    if (req.is_shared is not None or req.collaborators is not None) and not is_owner_or_admin:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Only session owner or admin can configure sharing",
        )
    _check_reasoning_effort(req.metadata)
    requested_mode = (req.metadata or {}).get("permission_mode")
    if requested_mode is None:
        return
    if requested_mode not in PERMISSION_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"permission_mode must be one of: {', '.join(PERMISSION_MODES)}",
        )
    if not is_owner_or_admin:
        # A collaborator may use the session but not loosen the owner's supervision.
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Only the session owner or an admin can change the permission mode",
        )


async def _usable_agent(
    request: Request, org_id: str, user_id: str, roles: list[str], agent_id: str | None
) -> None:
    """403 for an agent the caller may not use (``agent_access``)."""
    try:
        await require_usable_agent(
            getattr(request.app.state, "db_manager", None), org_id, user_id, roles, agent_id
        )
    except AgentAccessDenied as err:
        raise HTTPException(status_code=403, detail=str(err)) from err


def _apply_patch(record: SessionRecord, req: UpdateSessionRequest) -> None:
    meta = dict(record.metadata) if isinstance(record.metadata, dict) else {}
    if req.title is not None:
        record.title = req.title
        # A manual rename is authoritative: the AI titler must never overwrite it.
        meta["title_source"] = TITLE_SOURCE_USER
    if req.agent_id is not None:
        meta["agent_id"] = req.agent_id
    if req.is_shared is not None:
        record.is_shared = req.is_shared
        meta["is_shared"] = req.is_shared
    if req.collaborators is not None:
        record.collaborators = req.collaborators
        meta["collaborators"] = req.collaborators
    if req.checklist is not None:
        record.checklist = req.checklist
        meta["checklist"] = req.checklist
    # The transcript is server-owned (it is the agent's memory); clients cannot rewrite it.
    if req.metadata is not None:
        meta.update({k: v for k, v in req.metadata.items() if k in _CLIENT_PATCHABLE_METADATA})
    record.metadata = meta


@router.post("/sessions/{session_id}/share", response_model=SessionResponse)
async def share_session(
    session_id: str, req: ShareSessionRequest, request: Request
) -> SessionResponse:
    """Share session across organization members or configure specific collaborators."""
    store: SessionStoreProtocol = request.app.state.session_store
    req_org, req_user, roles = _get_request_identity(request)
    if hasattr(store, "get_session"):
        try:
            record = await store.get_session(session_id, tenant_org_id=req_org)
        except TypeError:
            record = await store.get_session(session_id)
    else:
        record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    if not is_admin(roles) and record.tenant_user_id != req_user:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Only session owner or admin can configure sharing",
        )

    updated = await store.mutate_session(
        session_id,
        record.tenant_org_id,
        lambda current: apply_sharing(current, req.is_shared, req.collaborators),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _to_response(updated)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict[str, Any]:
    """Delete a session record and tear down any associated sandbox workspace."""
    store: SessionStoreProtocol = request.app.state.session_store

    req_org, req_user, roles = _get_request_identity(request)
    if hasattr(store, "get_session"):
        try:
            record = await store.get_session(session_id, tenant_org_id=req_org)
        except TypeError:
            record = await store.get_session(session_id)
    else:
        record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")

    if not is_admin(roles) and record.tenant_user_id != req_user:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Only session owner or admin can delete session",
        )

    deleted = await _purge_session(request, record)
    return {"deleted": deleted, "session_id": session_id}


async def _purge_session(request: Request, record: SessionRecord) -> bool:
    """Delete one session with its sandbox workspace, transcript and cached derivatives."""
    store: SessionStoreProtocol = request.app.state.session_store
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)
    session_id = record.session_id
    if driver:
        try:
            await driver.destroy_workspace(session_id)
        except Exception as err:
            logger.warning("Error destroying workspace for deleted session %s: %s", session_id, err)

    # Release the session's cached semantic index: the workspace it described is gone,
    # and holding it would keep derived data alive for a session that no longer exists.
    code_index_cache = getattr(request.app.state, "code_index_cache", None)
    if code_index_cache is not None:
        code_index_cache.invalidate(session_id)
    forget_session_offloads(session_id)

    # Drop the replay history and sequence counter for the deleted session.
    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus is not None and hasattr(event_bus, "evict"):
        event_bus.evict(session_id)

    transcripts = getattr(request.app.state, "transcript_store", None)
    if transcripts is not None:
        await transcripts.delete(session_id, record.tenant_org_id)

    return await store.delete_session(session_id, tenant_org_id=record.tenant_org_id)


@router.post("/sessions/cleanup")
async def cleanup_database_and_sessions(request: Request) -> dict[str, Any]:
    """Purge sessions, destroy orphan sandbox workspaces, and perform database maintenance."""
    store: SessionStoreProtocol = request.app.state.session_store
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)

    org_id, _, _ = _require_admin(request)

    cleaned_count = 0
    sessions = await store.list_sessions(tenant_org_id=org_id, limit=200)
    for s in sessions:
        if driver:
            try:
                await driver.destroy_workspace(s.session_id)
            except Exception:
                pass
        await store.delete_session(s.session_id)
        cleaned_count += 1

    return {"status": "ok", "cleaned_sessions": cleaned_count}


@router.post("/sessions/reset", response_model=ResetSessionsResponse)
async def reset_all_sessions(request: Request) -> ResetSessionsResponse:
    """Delete every session the caller owns and seat them in a fresh blank session.

    Only the caller's own sessions go, administrators included: sessions shared with them
    and other members' sessions are kept.
    """
    store: SessionStoreProtocol = request.app.state.session_store
    org_id, user_id, _ = _get_request_identity(request)

    deleted_count = 0
    attempted: set[str] = set()
    while True:
        # Re-list after each batch so an owner with more sessions than one page loses them all;
        # `attempted` stops the loop on a session the store refused to delete.
        owned = await store.list_sessions(
            tenant_org_id=org_id, tenant_user_id=user_id, limit=RESET_BATCH
        )
        batch = [record for record in owned if record.session_id not in attempted]
        if not batch:
            break
        for record in batch:
            attempted.add(record.session_id)
            if await _purge_session(request, record):
                deleted_count += 1

    new_session_id = f"session_{uuid.uuid4().hex[:10]}"
    new_record = SessionRecord(
        session_id=new_session_id,
        tenant_org_id=org_id,
        tenant_user_id=user_id,
        title="New Mission",
        source=SessionSource.WEB_UI,
        sandbox_status=SandboxStatus.NON_EXISTENT,
        metadata={"created_via": "session_reset", "checklist": []},
    )
    await store.create_session(new_record)
    return ResetSessionsResponse(
        status="ok",
        deleted_count=deleted_count,
        new_session=_to_response(new_record),
    )
