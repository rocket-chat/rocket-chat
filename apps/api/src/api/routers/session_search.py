"""Search inside one session's transcript.

The cockpit holds only the newest messages of a long conversation, so a search that only looked
at what is loaded would miss most of it. The server searches the whole stored history and
returns where each match is (its index in the history) with a short excerpt; the cockpit loads
the history down to a match before scrolling to it.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.auth.access import _check_session_access, _get_request_identity
from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import SessionStoreProtocol

router = APIRouter(prefix="/v1", tags=["Sessions"])

MAX_MATCHES = 200
EXCERPT_BEFORE = 60
EXCERPT_AFTER = 100


class TranscriptMatch(BaseModel):
    """One message of the transcript that contains the searched text."""

    index: int = Field(..., description="Position of the message in the conversation history")
    role: str = Field(..., description="user, assistant, system or tool")
    excerpt: str = Field(..., description="The text around the first occurrence, trimmed")
    match_start: int = Field(..., description="Where the occurrence starts inside the excerpt")
    match_length: int = Field(..., description="Length of the occurrence inside the excerpt")
    occurrences: int = Field(..., description="How many times the message contains the text")


class TranscriptSearchResult(BaseModel):
    """The matches of a transcript search, oldest first."""

    query: str
    matches: list[TranscriptMatch]
    total_messages: int = Field(..., description="Messages in the whole conversation history")
    truncated: bool = Field(..., description="More messages matched than were returned")


def _excerpt(text: str, start: int, length: int) -> tuple[str, int]:
    """The text around one occurrence on a single line, and where the occurrence sits in it."""
    begin = max(0, start - EXCERPT_BEFORE)
    end = min(len(text), start + length + EXCERPT_AFTER)
    prefix = "…" if begin > 0 else ""
    suffix = "…" if end < len(text) else ""
    body = text[begin:end].replace("\n", " ")
    return f"{prefix}{body}{suffix}", len(prefix) + (start - begin)


def _match(index: int, message: ChatMessage, needle: str) -> TranscriptMatch | None:
    text = message.content or ""
    lowered = text.lower()
    start = lowered.find(needle)
    if start < 0:
        return None
    excerpt, offset = _excerpt(text, start, len(needle))
    return TranscriptMatch(
        index=index,
        role=message.role,
        excerpt=excerpt,
        match_start=offset,
        match_length=len(needle),
        occurrences=lowered.count(needle),
    )


@router.get("/sessions/{session_id}/search", response_model=TranscriptSearchResult)
async def search_transcript(
    session_id: str,
    request: Request,
    q: str = Query(..., min_length=1, max_length=200, description="Text to find, any case"),
    limit: int = Query(100, ge=1, le=MAX_MATCHES, description="Most matches returned"),
) -> TranscriptSearchResult:
    """Find the messages of a session's whole history that contain ``q`` (case-insensitive)."""
    store: SessionStoreProtocol = request.app.state.session_store
    req_org, req_user, roles = _get_request_identity(request)
    record = await store.get_session(session_id, tenant_org_id=req_org)
    if record is None or record.tenant_org_id != req_org:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, req_user, roles, require_write=False)

    needle = q.strip().lower()
    history = record.conversation_history or []
    matches: list[TranscriptMatch] = []
    truncated = False
    for index, message in enumerate(history):
        found = _match(index, message, needle) if needle else None
        if found is None:
            continue
        if len(matches) == limit:
            truncated = True
            break
        matches.append(found)
    return TranscriptSearchResult(
        query=q, matches=matches, total_messages=len(history), truncated=truncated
    )
