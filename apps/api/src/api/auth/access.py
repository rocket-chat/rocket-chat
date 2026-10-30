"""Identity and session-access helpers shared by every router."""

import logging

from fastapi import (
    HTTPException,
    Request,
)

from api.auth.identity import resolve_identity
from api.auth.roles import can_write, is_admin
from specifications.interfaces.session import (
    SessionRecord,
)

logger = logging.getLogger(__name__)


def _get_request_identity(request: Request) -> tuple[str, str, list[str]]:
    """Extract (org_id, user_id, roles) from the single identity resolver, or raise 401."""
    ctx = resolve_identity(request)
    if ctx is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return ctx.org_id, ctx.user_id, list(ctx.roles)


def _require_admin(request: Request) -> tuple[str, str, list[str]]:
    """Identity of a caller holding the ``admin`` or ``owner`` role, else 403."""
    org_id, user_id, roles = _get_request_identity(request)
    if not is_admin(roles):
        raise HTTPException(status_code=403, detail="Forbidden: Admin or owner role required")
    return org_id, user_id, roles


def _check_session_access(
    record: SessionRecord,
    user_id: str,
    roles: list[str],
    require_write: bool = False,
) -> None:
    """Enforce role permissions and user-specific isolation with sharing access."""
    if is_admin(roles):
        return
    if not can_write(roles) and require_write:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: viewers cannot perform write actions on session",
        )
    is_owner = record.tenant_user_id == user_id
    is_shared = getattr(record, "is_shared", False) or bool(
        isinstance(record.metadata, dict) and record.metadata.get("is_shared", False)
    )
    collabs = getattr(record, "collaborators", []) or (
        record.metadata.get("collaborators", []) if isinstance(record.metadata, dict) else []
    )
    is_collab = user_id in collabs
    if not (is_owner or is_shared or is_collab):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: You do not have permission to access this session",
        )
