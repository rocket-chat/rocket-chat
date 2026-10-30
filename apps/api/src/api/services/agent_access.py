"""Who may use an agent: one check for every place an agent is listed, chosen or run.

* A disabled agent is usable by nobody.
* A personal agent is usable by its owner only.
* An organization agent with no grants is usable by every member, guests excluded (the
  behaviour before grants existed). With grants, by the people, roles or "everyone" they name,
  and by guests (Slack guests, guest API tokens) only through a ``guests`` grant.
* Administrators may always use every organization agent.

The check applies to the person who *sends* the message (the requester), not the session's
owner: a collaborator in a shared session uses the agent under their own grants.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sqlalchemy import delete, select, update

from api.auth.api_tokens import GUEST_ID_PREFIX
from api.auth.roles import GUEST, canonical_role, is_admin, roles_for
from api.db.models import AgentGrantModel, ScheduledMissionModel
from api.services.memberships import role_in
from api.services.session_templates import SessionTemplate


class UsableAgent(Protocol):
    """What an access check reads of an agent (``AgentPersonaModel``)."""

    @property
    def id(self) -> str: ...
    @property
    def enabled(self) -> bool: ...
    @property
    def scope(self) -> str: ...
    @property
    def user_id(self) -> str | None: ...


PrincipalKind = Literal["everyone", "member", "role", "guests"]
PRINCIPAL_KINDS: tuple[PrincipalKind, ...] = ("everyone", "member", "role", "guests")
SLACK_GUEST_PREFIX = "slack:"


class AgentAccessDenied(PermissionError):
    """The person may not use the agent (never granted, or revoked)."""

    def __init__(self, agent_id: str, agent_name: str, *, disabled: bool = False) -> None:
        name = agent_name or agent_id
        super().__init__(
            f'The agent "{name}" has been disabled by an administrator. Pick another agent for '
            "this conversation."
            if disabled
            else f'You do not have access to the agent "{name}". '
            "Pick another agent for this conversation, or ask an administrator for access."
        )
        self.agent_id = agent_id
        self.agent_name = agent_name


@dataclass(frozen=True)
class Principal:
    """The person an access check is about."""

    user_id: str
    roles: tuple[str, ...]

    @property
    def is_guest(self) -> bool:
        return (
            GUEST in self.roles
            or self.user_id.startswith(SLACK_GUEST_PREFIX)
            or self.user_id.startswith(GUEST_ID_PREFIX)
        )

    @property
    def is_admin(self) -> bool:
        return not self.is_guest and is_admin(list(self.roles))

    @property
    def role(self) -> str:
        return canonical_role(self.roles[0]) if self.roles else "viewer"


@dataclass(frozen=True)
class Grant:
    id: str
    agent_id: str
    principal_kind: PrincipalKind
    principal_id: str
    created_by: str
    created_at: int


def _has_database(db_mgr: Any) -> bool:
    return db_mgr is not None and getattr(db_mgr, "session_factory", None) is not None


# Without a database (development): org -> agent -> grants.
_memory: dict[str, dict[str, list[Grant]]] = {}


def reset_memory_grants() -> None:
    _memory.clear()


async def principal_for(
    db_mgr: Any, org_id: str, user_id: str, roles: list[str] | None = None
) -> Principal:
    """The person, with the roles a request already proved or their stored role."""
    if roles is not None:
        return Principal(user_id, tuple(roles))
    if user_id.startswith((SLACK_GUEST_PREFIX, GUEST_ID_PREFIX)):
        return Principal(user_id, (GUEST,))
    if not _has_database(db_mgr):
        return Principal(user_id, tuple(roles_for("member")))
    async with db_mgr.session(tenant_org_id=org_id) as session:
        role = await role_in(session, user_id, org_id)
    return Principal(user_id, tuple(roles_for(role)) if role else ())


def _kind(value: str) -> PrincipalKind:
    for kind in PRINCIPAL_KINDS:
        if kind == value:
            return kind
    raise ValueError(f"unknown agent grant principal {value!r}")


async def grants_for(db_mgr: Any, org_id: str, agent_id: str) -> list[Grant]:
    if not _has_database(db_mgr):
        return list(_memory.get(org_id, {}).get(agent_id, []))
    async with db_mgr.session(tenant_org_id=org_id) as session:
        rows = await session.execute(
            select(AgentGrantModel).where(
                AgentGrantModel.org_id == org_id, AgentGrantModel.agent_id == agent_id
            )
        )
        return [
            Grant(
                r.id,
                r.agent_id,
                _kind(r.principal_kind),
                r.principal_id,
                r.created_by,
                r.created_at,
            )
            for r in rows.scalars()
        ]


async def set_grants(
    db_mgr: Any,
    org_id: str,
    agent_id: str,
    grants: list[tuple[PrincipalKind, str]],
    created_by: str,
) -> list[Grant]:
    """Replace the agent's grants (an empty list restores "every member")."""
    now = int(time.time())
    unique = list(
        dict.fromkeys((kind, pid if kind in ("member", "role") else "") for kind, pid in grants)
    )
    made = [
        Grant(f"agr_{uuid.uuid4().hex[:16]}", agent_id, kind, pid, created_by, now)
        for kind, pid in unique
    ]
    if not _has_database(db_mgr):
        _memory.setdefault(org_id, {})[agent_id] = made
        return made
    async with db_mgr.session(tenant_org_id=org_id) as session:
        await session.execute(
            delete(AgentGrantModel).where(
                AgentGrantModel.org_id == org_id, AgentGrantModel.agent_id == agent_id
            )
        )
        for grant in made:
            session.add(
                AgentGrantModel(
                    id=grant.id,
                    org_id=org_id,
                    agent_id=agent_id,
                    principal_kind=grant.principal_kind,
                    principal_id=grant.principal_id,
                    created_by=created_by,
                    created_at=now,
                )
            )
    return made


def _granted(principal: Principal, grants: list[Grant]) -> bool:
    if not grants:
        return not principal.is_guest
    for grant in grants:
        if grant.principal_kind == "guests" and principal.is_guest:
            return True
        if principal.is_guest:
            continue
        if grant.principal_kind == "everyone":
            return True
        if grant.principal_kind == "member" and grant.principal_id == principal.user_id:
            return True
        if grant.principal_kind == "role" and canonical_role(grant.principal_id) == principal.role:
            return True
    return False


async def may_use(
    db_mgr: Any, org_id: str, principal: Principal, agent: UsableAgent | None
) -> bool:
    """Whether ``principal`` may use ``agent`` (an ``AgentPersonaModel``)."""
    if agent is None or agent.enabled is False:
        return False
    if agent.scope == "user" or agent.user_id:
        return agent.user_id == principal.user_id
    if principal.is_admin:
        return True
    return _granted(principal, await grants_for(db_mgr, org_id, str(agent.id)))


MISSION_BLOCKED_STATUS = "blocked: agent access revoked"
TEMPLATE_BLOCKED_REASON = (
    "The agent this template names is no longer available to you; ask an administrator for "
    "access, or recreate the template with another agent."
)


async def templates_blocked_by(
    db_mgr: Any, org_id: str, agent: UsableAgent, templates: Sequence[SessionTemplate]
) -> list[str]:
    """The personal templates naming ``agent`` whose owner may no longer use it; their ids.

    Organization-wide templates are blocked per person and are flagged when listed
    (``blocked_for``): whoever still holds the agent keeps using them.
    """
    blocked: list[str] = []
    for template in templates:
        if template.agent_id != str(agent.id) or template.user_id is None:
            continue
        owner = await principal_for(db_mgr, org_id, template.user_id)
        if not await may_use(db_mgr, org_id, owner, agent):
            blocked.append(template.id)
    return blocked


async def blocked_for(
    db_mgr: Any,
    org_id: str,
    user_id: str,
    roles: list[str] | None,
    templates: Sequence[SessionTemplate],
) -> set[str]:
    """The agents named by ``templates`` that this person may not use (revoked or deleted)."""
    blocked: set[str] = set()
    for agent_id in {t.agent_id for t in templates if t.agent_id}:
        try:
            await require_usable_agent(db_mgr, org_id, user_id, roles, agent_id)
        except AgentAccessDenied:
            blocked.add(agent_id)
    return blocked


async def pause_missions_without_access(db_mgr: Any, org_id: str, agent: UsableAgent) -> list[str]:
    """Pause the scheduled missions whose owner may no longer use ``agent``; their ids.

    A mission runs as its owner, so one whose owner lost the agent would fail at every run;
    it is disabled with a status saying why, until its owner picks another agent.
    """
    if not _has_database(db_mgr):
        return []
    async with db_mgr.session(tenant_org_id=org_id) as session:
        missions = (
            await session.execute(
                select(ScheduledMissionModel.id, ScheduledMissionModel.user_id).where(
                    ScheduledMissionModel.org_id == org_id,
                    ScheduledMissionModel.agent_id == str(agent.id),
                    ScheduledMissionModel.enabled.is_(True),
                )
            )
        ).all()
    blocked = [
        mission_id
        for mission_id, owner in missions
        if not await may_use(db_mgr, org_id, await principal_for(db_mgr, org_id, owner), agent)
    ]
    if blocked:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            await session.execute(
                update(ScheduledMissionModel)
                .where(ScheduledMissionModel.id.in_(blocked))
                .values(enabled=False, last_status=MISSION_BLOCKED_STATUS)
            )
    return blocked


async def require_usable_agent(
    db_mgr: Any,
    org_id: str,
    user_id: str,
    roles: list[str] | None,
    agent_id: str | None,
    *,
    for_guests: bool = False,
) -> None:
    """Refuse (``AgentAccessDenied``) an agent the person may not use, when they choose it for
    a session, mission, template or bot. ``for_guests``: it must also serve Slack guests,
    because the bot answers people who have not paired."""
    if not agent_id:
        return
    # Imported here: the persona service imports this module.
    from api.services.personas import get_persona

    agent = await get_persona(db_mgr, org_id, user_id, agent_id, roles=roles)
    if agent is None:
        raise AgentAccessDenied(agent_id, "")
    if for_guests and not await may_use(
        db_mgr, org_id, Principal(f"{SLACK_GUEST_PREFIX}guest", (GUEST,)), agent
    ):
        raise AgentAccessDenied(
            agent_id,
            f"{agent.name} (not granted to guests, whom this bot answers before they pair)",
        )
