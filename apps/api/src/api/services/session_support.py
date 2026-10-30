"""Small helpers about session records shared by routers and the turn engine."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from api.models import (
    SessionResponse,
)
from api.services.repository_validation import (
    InvalidRepositoryError,
    parse_repository,
)
from specifications.interfaces.session import (
    SessionRecord,
    SessionStoreProtocol,
)

logger = logging.getLogger(__name__)


def _new_message_id() -> str:
    """Identifier for one accepted user instruction.

    Shared by the REST acceptance response and the ``user_message`` broadcast so a
    participant can dedupe the instruction it optimistically rendered itself.
    """
    return f"msg_{uuid.uuid4().hex[:16]}"


def _repo_slug(git_repo: str | None) -> str | None:
    if not git_repo:
        return None
    try:
        return parse_repository(git_repo).slug
    except InvalidRepositoryError:
        return None


ACTIVE_REPOSITORY_KEY = "active_repository"


def active_repository_of(session: SessionRecord) -> str:
    """The folder git, test and build tools default to: the one recorded, else the folder the
    repository named by the session's template, schedule or webhook is cloned into, else none."""
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    if recorded := str(metadata.get(ACTIVE_REPOSITORY_KEY) or ""):
        return recorded
    slug = _repo_slug(session.git_repo)
    return f"/workspace/{slug.rsplit('/', 1)[-1]}" if slug else ""


async def remember_active_repository(
    store: SessionStoreProtocol, session_id: str, org_id: str, folder: str
) -> None:
    """Record ``folder`` as the active repository unless the session already has one."""

    def set_if_missing(record: SessionRecord) -> None:
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        if not metadata.get(ACTIVE_REPOSITORY_KEY):
            metadata[ACTIVE_REPOSITORY_KEY] = folder
            record.metadata = metadata

    await store.mutate_session(session_id, org_id, set_if_missing)


async def choose_active_repository(
    store: SessionStoreProtocol, session_id: str, org_id: str, folder: str
) -> None:
    """Make ``folder`` the session's active repository, replacing any earlier choice."""

    def choose(record: SessionRecord) -> None:
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        metadata[ACTIVE_REPOSITORY_KEY] = folder
        record.metadata = metadata

    await store.mutate_session(session_id, org_id, choose)


def apply_sharing(record: SessionRecord, is_shared: bool, collaborators: list[str]) -> None:
    """Share ``record`` (a ``mutate_session`` step: it changes nothing else)."""
    record.is_shared = is_shared
    record.collaborators = collaborators
    metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
    metadata["is_shared"] = is_shared
    metadata["collaborators"] = collaborators
    record.metadata = metadata


# Metadata keys a client may set on its own session. Everything else (toolsets, fork and
# Slack provenance, title provenance, suggestions) is written by the server only.
_CLIENT_PATCHABLE_METADATA = frozenset(
    {"inference_mode", "selected_model", "agent_id", "permission_mode", "reasoning_effort"}
)

SYNC_HISTORY_TAIL = 50
MAX_TAIL_TOOL_CALLS = 120
MIN_TAIL_MESSAGES = 4


def user_turns_in(messages: list[Any]) -> int:
    """How many of ``messages`` the person sent (model objects or plain dicts)."""
    return sum(
        1
        for m in messages
        if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
    )


def slice_history_tail(
    history: list[Any], tail: int = SYNC_HISTORY_TAIL, max_tool_calls: int = MAX_TAIL_TOOL_CALLS
) -> tuple[list[Any], bool]:
    """Return the newest ``tail`` messages, bounded by total tool calls so huge turns stay fast."""
    total = len(history)
    if total == 0:
        return [], False
    candidates = history[-tail:] if total > tail else list(history)
    has_older = total > len(candidates)
    if len(candidates) <= MIN_TAIL_MESSAGES:
        return candidates, has_older
    kept: list[Any] = []
    tool_count = 0
    for idx, msg in enumerate(reversed(candidates)):
        calls = getattr(msg, "tool_calls", None) or (
            msg.get("tool_calls") if isinstance(msg, dict) else None
        )
        msg_tools = len(calls) if isinstance(calls, list) else 0
        if idx >= MIN_TAIL_MESSAGES and tool_count + msg_tools > max_tool_calls:
            has_older = True
            break
        kept.append(msg)
        tool_count += msg_tools
    kept.reverse()
    return kept, has_older


def _to_response(
    s: SessionRecord,
    history_override: list[Any] | None = None,
    has_older_history: bool = False,
    total_messages: int | None = None,
    user_turns_before: int = 0,
) -> SessionResponse:
    agent_id = None
    if isinstance(s.metadata, dict):
        agent_id = s.metadata.get("agent_id")

    source_history = s.conversation_history if history_override is None else history_override
    history = [
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
        for m in (source_history or [])
    ]

    is_shared = getattr(s, "is_shared", False)
    if not is_shared and isinstance(s.metadata, dict):
        is_shared = bool(s.metadata.get("is_shared", False))

    collaborators = getattr(s, "collaborators", []) or []
    if not collaborators and isinstance(s.metadata, dict):
        collaborators = s.metadata.get("collaborators", [])

    checklist = getattr(s, "checklist", []) or []
    if not checklist and isinstance(s.metadata, dict):
        checklist = s.metadata.get("checklist", [])

    suggested_followup = (
        s.metadata.get("suggested_followup") if isinstance(s.metadata, dict) else None
    )

    computed_total = (
        total_messages if total_messages is not None else len(s.conversation_history or [])
    )

    return SessionResponse(
        session_id=s.session_id,
        tenant_org_id=s.tenant_org_id,
        tenant_user_id=s.tenant_user_id,
        title=s.title,
        source=s.source.value if hasattr(s.source, "value") else str(s.source),
        created_at=s.created_at,
        updated_at=s.updated_at,
        sandbox_status=s.sandbox_status.value
        if hasattr(s.sandbox_status, "value")
        else str(s.sandbox_status),
        is_processing=s.is_processing,
        agent_id=agent_id,
        container_image=s.container_image,
        git_repo=s.git_repo,
        git_branch=s.git_branch,
        is_shared=is_shared,
        collaborators=collaborators,
        checklist=checklist,
        metadata=s.metadata,
        suggested_followup=suggested_followup,
        conversation_history=history,
        has_older_history=has_older_history,
        total_messages=computed_total,
        user_turns_before=user_turns_before,
    )


def _session_toolsets(session: SessionRecord) -> frozenset[str]:
    """Toolsets enabled on a session (e.g. ``{"slack"}``), from its metadata."""
    meta = session.metadata if isinstance(session.metadata, dict) else {}
    raw = meta.get("toolsets")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(item) for item in raw)
