"""Small helpers about session records shared by routers and the turn engine."""

import logging
import uuid

from api.models import (
    SessionResponse,
)
from api.services.repository_validation import (
    InvalidRepositoryError,
    parse_repository,
)
from specifications.interfaces.session import (
    SessionRecord,
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


# Metadata keys a client may set on its own session. Everything else (toolsets, fork and
# Slack provenance, title provenance, suggestions) is written by the server only.
_CLIENT_PATCHABLE_METADATA = frozenset(
    {"inference_mode", "selected_model", "agent_id", "permission_mode"}
)


def _to_response(s: SessionRecord) -> SessionResponse:
    agent_id = None
    if isinstance(s.metadata, dict):
        agent_id = s.metadata.get("agent_id")

    history = [
        {
            "role": m.role,
            "content": m.content or "",
            "reasoning": getattr(m, "reasoning", None),
            "tool_calls": getattr(m, "tool_calls", None),
            "author_user_id": getattr(m, "author_user_id", None),
        }
        for m in (s.conversation_history or [])
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
    )


def _session_toolsets(session: SessionRecord) -> frozenset[str]:
    """Toolsets enabled on a session (e.g. ``{"slack"}``), from its metadata."""
    meta = session.metadata if isinstance(session.metadata, dict) else {}
    raw = meta.get("toolsets")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(item) for item in raw)
