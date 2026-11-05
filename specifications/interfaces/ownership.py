"""Who owns an object, and what the people of an organization may do with it.

Every shareable object (an agent, a skill, an MCP server, a knowledge base, ...) has one
**owner**: a member (``user``), a team of the organization (``team``) or the organization
itself (``org``). The owner decides who can see and use the object and who can change it:

* ``user``: its member, and organization administrators (who may manage anything);
* ``team``: the team's members use it; the team's administrators and the organization's
  administrators change it;
* ``org``: every member uses it; organization administrators change it.

Teams are an authorisation layer inside one organization: row-level security still isolates
organizations, and nothing here ever crosses one. Moving an object is changing its owner, which
needs the right to manage it where it is and where it goes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

OwnerScope = Literal["user", "team", "org"]
OWNER_SCOPES: tuple[OwnerScope, ...] = ("user", "team", "org")
TeamRole = Literal["admin", "member"]
TEAM_ROLES: tuple[TeamRole, ...] = ("admin", "member")


@dataclass(frozen=True)
class Owner:
    scope: OwnerScope
    # The member for ``user``; ``None`` otherwise.
    user_id: str | None = None
    # The team for ``team``; ``None`` otherwise.
    team_id: str | None = None


@dataclass(frozen=True)
class Viewer:
    """A member as ownership checks see them: their organization role and their teams."""

    user_id: str
    is_org_admin: bool = False
    # Every team the member belongs to, and the ones among them they administer.
    teams: frozenset[str] = field(default_factory=frozenset)
    admin_teams: frozenset[str] = field(default_factory=frozenset)


def owner_problem(scope: str, user_id: str | None, team_id: str | None) -> str | None:
    """Why ``(scope, user_id, team_id)`` is not a well-formed owner, or ``None``."""
    if scope not in OWNER_SCOPES:
        return f"Unknown owner '{scope}': use one of {', '.join(OWNER_SCOPES)}."
    if scope == "user" and (not user_id or team_id):
        return "A personal object names its member and no team."
    if scope == "team" and (not team_id or user_id):
        return "A team object names its team and no member."
    if scope == "org" and (user_id or team_id):
        return "An organization object names neither a member nor a team."
    return None


def is_shared(scope: str) -> bool:
    """Whether more than one person uses objects of this scope (a team's or the organization's)."""
    return scope in ("team", "org")


def can_see(viewer: Viewer, owner: Owner) -> bool:
    """Whether ``viewer`` may list and use an object owned by ``owner``."""
    if owner.scope == "org":
        return True
    if owner.scope == "team":
        return viewer.is_org_admin or owner.team_id in viewer.teams
    return owner.user_id == viewer.user_id


def can_manage(viewer: Viewer, owner: Owner) -> bool:
    """Whether ``viewer`` may create, change or delete an object owned by ``owner``."""
    if viewer.is_org_admin:
        return True
    if owner.scope == "team":
        return owner.team_id in viewer.admin_teams
    return owner.scope == "user" and owner.user_id == viewer.user_id


def can_move(viewer: Viewer, source: Owner, destination: Owner) -> bool:
    """Whether ``viewer`` may give an object owned by ``source`` to ``destination``.

    A member never hands their own object to another member: the destination of a personal
    object is always the person moving it (administrators may move anything)."""
    if not (can_manage(viewer, source) and can_manage(viewer, destination)):
        return False
    return (
        destination.scope != "user" or destination.user_id == viewer.user_id or viewer.is_org_admin
    )
