"""The one place stores and routes apply object ownership (``specifications.interfaces.ownership``).

Every owned table carries ``scope`` (``user``, ``team`` or ``org``), ``user_id`` and ``team_id``.
``visible_clause`` is the SQL form of ``can_see`` for listing, ``visible`` its form for records
kept in memory, and ``owner_for_write`` decides the owner a create, update or move stores.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import and_, or_

from api.auth.access import _get_request_identity
from api.services.teams import get_team, viewer_for
from specifications.interfaces.ownership import (
    OWNER_SCOPES,
    Owner,
    Viewer,
    can_manage,
    can_move,
    can_see,
    owner_problem,
)


@dataclass
class OwnershipError(Exception):
    """A refused ownership change, carrying the HTTP status to report."""

    status_code: int
    detail: str

    def as_http(self) -> HTTPException:
        return HTTPException(status_code=self.status_code, detail=self.detail)


def _field(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def owner_of(record: Any) -> Owner:
    """The owner of a row, a model or an in-memory record (``scope`` defaults to ``org``)."""
    scope = str(_field(record, "scope") or "org")
    if scope == "user":
        return Owner("user", user_id=_field(record, "user_id"))
    if scope == "team":
        return Owner("team", team_id=_field(record, "team_id"))
    return Owner("org")


def team_of(record: Any) -> str | None:
    """The team that owns ``record`` (an agent: its turns run in that team's context)."""
    return owner_of(record).team_id if record is not None else None


def owner_columns(owner: Owner) -> dict[str, Any]:
    """The stored columns of ``owner``."""
    return {"scope": owner.scope, "user_id": owner.user_id, "team_id": owner.team_id}


def visible_clause(model: Any, viewer: Viewer) -> Any:
    """A SQL condition on ``model`` (an owned table): the rows ``viewer`` may see."""
    conditions = [
        model.scope == "org",
        and_(model.scope == "user", model.user_id == viewer.user_id),
    ]
    if viewer.is_org_admin:
        conditions.append(model.scope == "team")
    elif viewer.teams:
        conditions.append(and_(model.scope == "team", model.team_id.in_(sorted(viewer.teams))))
    return or_(*conditions)


def visible(record: Any, viewer: Viewer) -> bool:
    return can_see(viewer, owner_of(record))


def manageable(record: Any, viewer: Viewer) -> bool:
    return can_manage(viewer, owner_of(record))


def require_manage(viewer: Viewer, owner: Owner, what: str) -> None:
    if not can_manage(viewer, owner):
        raise OwnershipError(403, _refusal(owner, what))


def _refusal(owner: Owner, what: str) -> str:
    if owner.scope == "org":
        return f"Only organization administrators can change organization {what}."
    if owner.scope == "team":
        return f"Only the team's administrators can change its {what}."
    return f"Only its owner can change this {what.rstrip('s')}."


async def requested_owner(
    db_mgr: Any, org_id: str, viewer: Viewer, scope: str, team_id: str | None
) -> Owner:
    """The owner a request names (a personal one is always the requester's), validated."""
    member = viewer.user_id if scope == "user" else None
    team = (team_id or None) if scope == "team" else None
    if problem := owner_problem(scope, member, team):
        raise OwnershipError(422, problem)
    owner = Owner(next(s for s in OWNER_SCOPES if s == scope), user_id=member, team_id=team)
    if owner.team_id and await get_team(db_mgr, org_id, owner.team_id) is None:
        raise OwnershipError(422, "That team does not exist in this organization.")
    return owner


async def owner_for_write(
    db_mgr: Any,
    org_id: str,
    viewer: Viewer,
    *,
    scope: str,
    team_id: str | None,
    current: Owner | None,
    what: str,
) -> Owner:
    """The owner to store: ``current`` when the request keeps it, else the requested one, once
    ``viewer`` may manage the object where it is and where it goes.

    An update that names the scope it already has keeps the current owner, so an administrator
    editing a member's personal object never takes it over."""
    if (
        current is not None
        and scope == current.scope
        and (scope != "team" or not team_id or team_id == current.team_id)
    ):
        require_manage(viewer, current, what)
        return current
    destination = await requested_owner(db_mgr, org_id, viewer, scope, team_id)
    if current is None:
        require_manage(viewer, destination, what)
        return destination
    if not can_move(viewer, current, destination):
        raise OwnershipError(
            403, f"You cannot move these {what} there: you need to manage both owners."
        )
    return destination


async def request_viewer(request: Request) -> tuple[str, Viewer]:
    """The organization and the ownership view of the caller."""
    org_id, user_id, roles = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    return org_id, await viewer_for(db_mgr, org_id, user_id, roles)
