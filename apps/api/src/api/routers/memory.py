"""Long-term-memory inspection API.

The cockpit memory inspector needs to *view* what the agent has deliberately stored and
*forget* an entry. Memory is a durability feature, so without PostgreSQL there is nothing
to inspect: the route returns 503 rather than an empty 200 that would read as "no memories".
Reads and deletes are tenant-scoped by the service, which opens its session under the RLS
context, so one organisation can never see or delete another's memories.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity
from api.db.session import DatabaseSessionManager
from api.services.memory import MEMORY_SCOPES, MemoryRecord, MemoryService

router = APIRouter(prefix="/v1", tags=["Memory"])


class MemoryResponse(BaseModel):
    """One durable memory the agent wrote, as shown in the cockpit inspector."""

    memory_id: str = Field(..., description="Memory identifier")
    scope: str = Field(..., description="'user', 'org' or 'session'")
    kind: str = Field(..., description="'fact', 'preference', 'decision' or 'convention'")
    content: str = Field(..., description="The remembered statement")
    confidence: float = Field(..., description="Writer-assigned confidence, 0..1")
    source_session_id: str | None = Field(
        default=None, description="Session the memory was learned in, when known"
    )
    created_at: int = Field(..., description="Creation time, epoch seconds")
    last_used_at: int | None = Field(
        default=None, description="Last recall time, epoch seconds (drives decay ranking)"
    )


class MemoryListResponse(BaseModel):
    """A user's visible memories, newest first."""

    memories: list[MemoryResponse] = Field(default_factory=list, description="Visible memories")
    total: int = Field(..., description="Number of memories returned")


class ForgetResponse(BaseModel):
    """Confirmation that a memory was removed."""

    forgotten: bool = Field(..., description="True once the memory no longer exists")
    memory_id: str = Field(..., description="Memory identifier that was forgotten")


def _to_response(record: MemoryRecord) -> MemoryResponse:
    return MemoryResponse(
        memory_id=record.id,
        scope=record.scope,
        kind=record.kind,
        content=record.content,
        confidence=record.confidence,
        source_session_id=record.source_session_id,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
    )


def _require_service(request: Request) -> MemoryService:
    db_manager: DatabaseSessionManager | None = getattr(request.app.state, "db_manager", None)
    if db_manager is None:
        # Memory rows are durable; an empty 200 without a database would be misleading.
        raise HTTPException(
            status_code=503,
            detail="Long-term memory requires the PostgreSQL-backed session store",
        )
    return MemoryService(db_manager)


@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(
    request: Request,
    scope: str | None = Query(default=None, description="Optional scope filter"),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum memories to return"),
) -> MemoryListResponse:
    """List the caller's visible long-term memories for the cockpit inspector."""
    if scope is not None and scope not in MEMORY_SCOPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown scope '{scope}'. Expected one of: {', '.join(MEMORY_SCOPES)}.",
        )
    org_id, user_id, _roles = _get_request_identity(request)
    service = _require_service(request)
    records = await service.list_memories(org_id=org_id, user_id=user_id, scope=scope, limit=limit)
    return MemoryListResponse(
        memories=[_to_response(record) for record in records],
        total=len(records),
    )


@router.delete("/memories/{memory_id}", response_model=ForgetResponse)
async def forget_memory(memory_id: str, request: Request) -> ForgetResponse:
    """Delete one memory the caller can see (the inspector's "forget" action)."""
    org_id, user_id, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Read-only role cannot forget memories",
        )
    service = _require_service(request)
    forgotten = await service.forget(org_id=org_id, user_id=user_id, memory_id=memory_id)
    if not forgotten:
        raise HTTPException(status_code=404, detail="Memory not found")
    return ForgetResponse(forgotten=True, memory_id=memory_id)
