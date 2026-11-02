"""An organization's teams, their members, and the ownership view of a member.

Organization administrators create, rename and delete teams; a team's administrators (and the
organization's) manage its members and everything the team owns. ``teams`` and
``team_memberships`` are under forced RLS; without a database (development and tests) they live
in per-organization memory.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from api.auth.roles import can_write, is_admin
from api.db.models import (
    AgentPersonaEntityModel,
    CustomSkillModel,
    KnowledgeBaseModel,
    McpServerEntityModel,
    TeamMembershipModel,
    TeamModel,
    UserModel,
)
from api.services.memberships import belongs_to, role_in
from specifications.interfaces.ownership import TEAM_ROLES, Viewer

MAX_TEAMS_PER_ORG = 200
# The tables whose rows a team may own, by the name the API reports them under.
OWNED_TABLES: dict[str, Any] = {
    "agents": AgentPersonaEntityModel,
    "skills": CustomSkillModel,
    "mcp_servers": McpServerEntityModel,
    "knowledge_bases": KnowledgeBaseModel,
}


@dataclass(frozen=True)
class Team:
    id: str
    org_id: str
    name: str
    description: str = ""
    member_count: int = 0
    created_at: int = 0


@dataclass(frozen=True)
class TeamMember:
    user_id: str
    email: str
    name: str
    role: str


@dataclass
class TeamError(Exception):
    """A refused team operation, carrying the HTTP status to report."""

    status_code: int
    detail: str


def _has_database(db_mgr: Any) -> bool:
    return db_mgr is not None and getattr(db_mgr, "session_factory", None) is not None


# Without a database: org -> team id -> team; org -> team id -> user id -> role.
_memory_teams: dict[str, dict[str, Team]] = {}
_memory_members: dict[str, dict[str, dict[str, str]]] = {}
# Owned objects without a database, registered by the stores that keep them in memory:
# org -> team id -> kind -> count.
_memory_owned: dict[str, dict[str, dict[str, int]]] = {}


def reset_memory_teams() -> None:
    _memory_teams.clear()
    _memory_members.clear()
    _memory_owned.clear()


def note_memory_owned(org_id: str, team_id: str, kind: str, delta: int) -> None:
    """Keep the in-memory count of a team's objects (development stores only)."""
    counts = _memory_owned.setdefault(org_id, {}).setdefault(team_id, {})
    counts[kind] = max(0, counts.get(kind, 0) + delta)


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise TeamError(422, "A team needs a name.")
    return cleaned[:80]


async def list_teams(db_mgr: Any, org_id: str) -> list[Team]:
    if not _has_database(db_mgr):
        members = _memory_members.get(org_id, {})
        return sorted(
            (
                Team(
                    t.id, t.org_id, t.name, t.description, len(members.get(t.id, {})), t.created_at
                )
                for t in _memory_teams.get(org_id, {}).values()
            ),
            key=lambda t: t.name.lower(),
        )
    async with db_mgr.session(tenant_org_id=org_id) as session:
        counts = dict(
            (
                await session.execute(
                    select(TeamMembershipModel.team_id, func.count())
                    .where(TeamMembershipModel.org_id == org_id)
                    .group_by(TeamMembershipModel.team_id)
                )
            ).all()
        )
        rows = (
            await session.execute(select(TeamModel).where(TeamModel.org_id == org_id))
        ).scalars()
        teams = [
            Team(r.id, r.org_id, r.name, r.description, int(counts.get(r.id, 0)), r.created_at)
            for r in rows
        ]
    return sorted(teams, key=lambda t: t.name.lower())


async def get_team(db_mgr: Any, org_id: str, team_id: str) -> Team | None:
    return next((t for t in await list_teams(db_mgr, org_id) if t.id == team_id), None)


async def create_team(
    db_mgr: Any, org_id: str, name: str, description: str, created_by: str
) -> Team:
    teams = await list_teams(db_mgr, org_id)
    if len(teams) >= MAX_TEAMS_PER_ORG:
        raise TeamError(409, f"An organization has at most {MAX_TEAMS_PER_ORG} teams.")
    team = Team(
        f"team_{uuid.uuid4().hex[:12]}",
        org_id,
        _clean_name(name),
        description.strip()[:255],
        0,
        int(time.time()),
    )
    if any(t.name.lower() == team.name.lower() for t in teams):
        raise TeamError(409, f"A team named {team.name!r} already exists.")
    if not _has_database(db_mgr):
        _memory_teams.setdefault(org_id, {})[team.id] = team
        return team
    try:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            session.add(
                TeamModel(
                    id=team.id,
                    org_id=org_id,
                    name=team.name,
                    description=team.description,
                    created_by=created_by,
                    created_at=team.created_at,
                )
            )
    except IntegrityError as err:
        raise TeamError(409, f"A team named {team.name!r} already exists.") from err
    return team


async def update_team(
    db_mgr: Any, org_id: str, team_id: str, name: str | None, description: str | None
) -> Team:
    team = await get_team(db_mgr, org_id, team_id)
    if team is None:
        raise TeamError(404, "Team not found.")
    new_name = _clean_name(name) if name is not None else team.name
    new_description = description.strip()[:255] if description is not None else team.description
    others = [t for t in await list_teams(db_mgr, org_id) if t.id != team_id]
    if any(t.name.lower() == new_name.lower() for t in others):
        raise TeamError(409, f"A team named {new_name!r} already exists.")
    updated = Team(team.id, org_id, new_name, new_description, team.member_count, team.created_at)
    if not _has_database(db_mgr):
        _memory_teams[org_id][team_id] = updated
        return updated
    async with db_mgr.session(tenant_org_id=org_id) as session:
        row = await session.get(TeamModel, team_id)
        if row is None or row.org_id != org_id:
            raise TeamError(404, "Team not found.")
        row.name, row.description = new_name, new_description
    return updated


async def owned_counts(db_mgr: Any, org_id: str, team_id: str) -> dict[str, int]:
    """How many objects of each kind the team owns (only kinds it owns some of)."""
    if not _has_database(db_mgr):
        counts = _memory_owned.get(org_id, {}).get(team_id, {})
        return {kind: count for kind, count in counts.items() if count}
    found: dict[str, int] = {}
    async with db_mgr.session(tenant_org_id=org_id) as session:
        for kind, model in OWNED_TABLES.items():
            count = (
                await session.execute(
                    select(func.count()).where(model.org_id == org_id, model.team_id == team_id)
                )
            ).scalar_one()
            if count:
                found[kind] = int(count)
    return found


async def delete_team(db_mgr: Any, org_id: str, team_id: str) -> None:
    """Delete an empty team; one that still owns objects is refused with what it owns."""
    if await get_team(db_mgr, org_id, team_id) is None:
        raise TeamError(404, "Team not found.")
    if owned := await owned_counts(db_mgr, org_id, team_id):
        listed = ", ".join(f"{count} {kind.replace('_', ' ')}" for kind, count in owned.items())
        raise TeamError(409, f"The team still owns {listed}: move or delete them first.")
    if not _has_database(db_mgr):
        _memory_teams.get(org_id, {}).pop(team_id, None)
        _memory_members.get(org_id, {}).pop(team_id, None)
        return
    async with db_mgr.session(tenant_org_id=org_id) as session:
        row = await session.get(TeamModel, team_id)
        if row is not None and row.org_id == org_id:
            await session.delete(row)


async def team_members(db_mgr: Any, org_id: str, team_id: str) -> list[TeamMember]:
    if await get_team(db_mgr, org_id, team_id) is None:
        raise TeamError(404, "Team not found.")
    if not _has_database(db_mgr):
        roles = _memory_members.get(org_id, {}).get(team_id, {})
        return [TeamMember(uid, "", uid, role) for uid, role in sorted(roles.items())]
    async with db_mgr.session(tenant_org_id=org_id) as session:
        rows = await session.execute(
            select(UserModel.id, UserModel.email, UserModel.name, TeamMembershipModel.role)
            .join(TeamMembershipModel, TeamMembershipModel.user_id == UserModel.id)
            .where(TeamMembershipModel.org_id == org_id, TeamMembershipModel.team_id == team_id)
        )
        found = [TeamMember(uid, email or "", name or "", role) for uid, email, name, role in rows]
    return sorted(found, key=lambda m: (m.role != "admin", (m.name or m.email).lower()))


async def set_team_member(
    db_mgr: Any, org_id: str, team_id: str, user_id: str, role: str
) -> TeamMember:
    """Add a member of the organization to the team, or change their role in it."""
    if role not in TEAM_ROLES:
        raise TeamError(422, f"A team role is one of {', '.join(TEAM_ROLES)}.")
    if await get_team(db_mgr, org_id, team_id) is None:
        raise TeamError(404, "Team not found.")
    if not _has_database(db_mgr):
        _memory_members.setdefault(org_id, {}).setdefault(team_id, {})[user_id] = role
        return TeamMember(user_id, "", user_id, role)
    async with db_mgr.session(tenant_org_id=org_id) as session:
        if await role_in(session, user_id, org_id) is None:
            raise TeamError(422, "Only members of the organization can join its teams.")
        row = (
            await session.execute(
                select(TeamMembershipModel).where(
                    TeamMembershipModel.team_id == team_id, TeamMembershipModel.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(
                TeamMembershipModel(
                    id=f"tm_{uuid.uuid4().hex[:16]}",
                    org_id=org_id,
                    team_id=team_id,
                    user_id=user_id,
                    role=role,
                    created_at=int(time.time()),
                )
            )
        else:
            row.role = role
        person = await session.get(UserModel, user_id)
    return TeamMember(
        user_id, getattr(person, "email", "") or "", getattr(person, "name", "") or "", role
    )


async def remove_team_member(db_mgr: Any, org_id: str, team_id: str, user_id: str) -> bool:
    if not _has_database(db_mgr):
        return _memory_members.get(org_id, {}).get(team_id, {}).pop(user_id, None) is not None
    async with db_mgr.session(tenant_org_id=org_id) as session:
        row = (
            await session.execute(
                select(TeamMembershipModel).where(
                    TeamMembershipModel.org_id == org_id,
                    TeamMembershipModel.team_id == team_id,
                    TeamMembershipModel.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        await session.delete(row)
    return True


async def teams_of(db_mgr: Any, org_id: str, user_id: str) -> dict[str, str]:
    """The teams the person belongs to in ``org_id``, with their role in each.

    Only a current member of the organization belongs to its teams (``belongs_to``), so a
    person removed from the organization keeps no team rights through a leftover row."""
    if not _has_database(db_mgr):
        return {
            team_id: roles[user_id]
            for team_id, roles in _memory_members.get(org_id, {}).items()
            if user_id in roles
        }
    async with db_mgr.session(tenant_org_id=org_id) as session:
        rows = await session.execute(
            select(TeamMembershipModel.team_id, TeamMembershipModel.role)
            .join(UserModel, UserModel.id == TeamMembershipModel.user_id)
            .where(
                TeamMembershipModel.org_id == org_id,
                TeamMembershipModel.user_id == user_id,
                belongs_to(org_id),
            )
        )
        return {team_id: role for team_id, role in rows}


async def viewer_for(db_mgr: Any, org_id: str, user_id: str, roles: list[str]) -> Viewer:
    """The member as ownership checks see them. A viewer (read-only role) administers no team."""
    teams = await teams_of(db_mgr, org_id, user_id)
    may_write = can_write(roles)
    return Viewer(
        user_id=user_id,
        is_org_admin=is_admin(roles),
        teams=frozenset(teams),
        admin_teams=frozenset(t for t, role in teams.items() if role == "admin" and may_write),
    )


async def member_viewer(db_mgr: Any, org_id: str, user_id: str) -> Viewer:
    """The person as a turn sees them: the objects of their own teams, never the administrator's
    view of every team (which serves managing them, not working with them)."""
    return Viewer(user_id=user_id, teams=frozenset(await teams_of(db_mgr, org_id, user_id)))
