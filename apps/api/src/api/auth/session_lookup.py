"""The session a request names, read for the caller: 404 when it is not theirs to see, 403 when
they may see it but not do what they asked."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from api.auth.access import _check_session_access, _get_request_identity
from api.auth.roles import is_admin
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol


@dataclass(frozen=True)
class SessionCaller:
    """A session and who is acting on it."""

    record: SessionRecord
    org_id: str
    user_id: str
    roles: list[str]

    @property
    def owns_or_administers(self) -> bool:
        """The session's owner or an administrator: who may change how it is supervised."""
        return is_admin(self.roles) or self.record.tenant_user_id == self.user_id


async def session_for_request(
    request: Request, session_id: str, *, require_write: bool
) -> SessionCaller:
    org_id, user_id, roles = _get_request_identity(request)
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    # Row-level security already hides other organizations' sessions on PostgreSQL; the
    # in-memory store does not, so the organization is compared here as well.
    if record is None or record.tenant_org_id != org_id:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, user_id, roles, require_write=require_write)
    return SessionCaller(record=record, org_id=org_id, user_id=user_id, roles=roles)
