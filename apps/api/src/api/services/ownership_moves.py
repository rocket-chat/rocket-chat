"""What moving an object to another owner would do, before it is moved.

``preview_move`` answers, for any owned object and a destination owner: who will use it
afterwards, whether the caller may move it (``can_move``), and what depends on it or on what it
depends: a dependency that would *break* (an agent bound to a team's key that leaves the team, a
key moved away from the agents bound to it, a sandbox type moved away from the agents and
missions that start in it, a mission whose runs could no longer use its agent) refuses the move
until it is fixed; one that would only *narrow* (a personal skill on an agent the whole team
uses) is said so the person can decide. The same checks guard the moves of keys and sandbox
types, which have no form of their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from api.db.models import CustomSkillModel, KnowledgeBaseModel, McpServerEntityModel
from api.models import AgentPersonaModel
from api.services.agent_access import may_use, mission_principal
from api.services.knowledge import _kb_fallback_store
from api.services.mcp_turn import _mcp_servers_store
from api.services.mission_delivery import mission_store
from api.services.model_credentials import ModelCredentialStore, StoredCredential
from api.services.ownership import owner_of
from api.services.personas import find_agent, listed_agents
from api.services.sandbox_catalogue import SandboxCatalogue, SandboxEntry
from api.services.session_templates import template_store
from api.services.skills_lookup import _memory_skills
from api.services.teams import get_team
from specifications.interfaces.ownership import Owner, Viewer, can_move

MOVABLE_KINDS = (
    "agent", "skill", "mcp_server", "knowledge_base", "credential", "sandbox", "mission",
    "template",
)  # fmt: skip


@dataclass(frozen=True)
class MoveProblem:
    kind: str
    id: str
    name: str
    detail: str
    # A move that breaks something is refused until it is fixed; a narrowing is only said.
    breaks: bool


@dataclass(frozen=True)
class MovePreview:
    audience: str
    allowed: bool
    reason: str = ""
    problems: list[MoveProblem] = field(default_factory=list)


class MoveError(LookupError):
    """The object does not exist (or the caller cannot see it)."""


def _credentials(app: Any) -> ModelCredentialStore:
    store = getattr(app.state, "model_credentials", None)
    if isinstance(store, ModelCredentialStore):
        return store
    return ModelCredentialStore(getattr(app.state, "db_manager", None))


def _all_agents_view(viewer: Viewer) -> Viewer:
    # Every organization and team agent, and the caller's own: what can depend on an object
    # they move.
    return Viewer(user_id=viewer.user_id, is_org_admin=True)


async def audience(db_mgr: Any, org_id: str, owner: Owner) -> str:
    if owner.scope == "org":
        return "Everyone in the organization"
    if owner.scope == "team":
        team = await get_team(db_mgr, org_id, owner.team_id or "")
        return f"The members of {team.name if team else 'the team'}"
    return "Only its owner"


def _wider(first: Owner, second: Owner) -> bool:
    """Whether ``first`` reaches people ``second`` does not."""
    if first.scope == "org":
        return second.scope != "org"
    if first.scope == "team":
        return second.scope == "user" or (
            second.scope == "team" and second.team_id != first.team_id
        )
    return second.scope != "user" or second.user_id != first.user_id


def _credential_owner(item: StoredCredential) -> Owner:
    if item.owner_kind == "member":
        return Owner("user", user_id=item.owner_user_id)
    if item.owner_kind == "team":
        return Owner("team", team_id=item.team_id)
    return Owner("org")


def _sandbox_owner(entry: SandboxEntry) -> Owner:
    return Owner("team", team_id=entry.team_id) if entry.team_id else Owner("org")


def _key_serves(key: Owner, agent: Owner, agent_id: str, item: StoredCredential) -> bool:
    """Whether a key owned by ``key`` still serves an agent owned by ``agent`` (the resolver's
    rule): a team's key its team's agents, a member's key their own or those they shared it
    with, an organization's key any shared agent."""
    if key.scope == "team":
        return agent.scope == "team" and agent.team_id == key.team_id
    if key.scope == "user":
        return agent.user_id == key.user_id or agent_id in item.shared_with_org_agents
    return agent.scope != "user"


# -- loading ---------------------------------------------------------------------------------


async def _row(db_mgr: Any, model: Any, org_id: str, object_id: str) -> Any:
    if db_mgr is None or getattr(db_mgr, "session_factory", None) is None:
        return None
    async with db_mgr.session(tenant_org_id=org_id) as session:
        found = await session.get(model, object_id)
        return found if found is not None and found.org_id == org_id else None


async def load_owner(
    app: Any, org_id: str, viewer: Viewer, kind: str, object_id: str
) -> tuple[Owner, str, Any]:
    """(owner, name, record) of an object, or ``MoveError``."""
    db_mgr = getattr(app.state, "db_manager", None)
    record: Any = None
    if kind == "agent":
        record = await find_agent(db_mgr, org_id, _all_agents_view(viewer), object_id)
    elif kind == "skill":
        record = await _row(db_mgr, CustomSkillModel, org_id, object_id) or _memory_skills.get(
            org_id, {}
        ).get(object_id)
    elif kind in ("knowledge_base", "mcp_server"):
        record = await _stored_row(app, db_mgr, kind, org_id, object_id)
    elif kind == "credential":
        item = await _credentials(app).get(org_id, object_id)
        if item is not None:
            return _credential_owner(item), item.label, item
    elif kind == "sandbox":
        entries = await SandboxCatalogue(db_mgr).entries(org_id, teams=None)
        entry = next((e for e in entries if e.id == object_id), None)
        if entry is not None:
            return _sandbox_owner(entry), entry.name, entry
    elif kind == "mission":
        record = await mission_store(app).get(org_id, object_id)
    elif kind == "template":
        record = next(
            (t for t in await template_store(app.state).all(org_id) if t.id == object_id), None
        )
    if record is None:
        raise MoveError(f"No {kind.replace('_', ' ')} {object_id}.")
    name = record.get("name") if isinstance(record, dict) else getattr(record, "name", object_id)
    return owner_of(record), str(name or object_id), record


async def _stored_row(app: Any, db_mgr: Any, kind: str, org_id: str, object_id: str) -> Any:
    model = KnowledgeBaseModel if kind == "knowledge_base" else McpServerEntityModel
    found = await _row(db_mgr, model, org_id, object_id)
    if found is not None:
        return found
    memory = _kb_fallback_store if kind == "knowledge_base" else _mcp_servers_store
    held = memory.get(object_id)
    return held if held is not None and held.get("org_id", org_id) == org_id else None


# -- what depends on what --------------------------------------------------------------------


async def _agent_problems(
    app: Any, org_id: str, agent: AgentPersonaModel, dest: Owner
) -> list[MoveProblem]:
    db_mgr = getattr(app.state, "db_manager", None)
    problems: list[MoveProblem] = []
    store = _credentials(app)
    for binding in agent.credential_bindings:
        item = await store.get(org_id, binding.credential_id)
        if item is not None and not _key_serves(_credential_owner(item), dest, agent.id, item):
            problems.append(
                MoveProblem(
                    "credential",
                    item.id,
                    item.label,
                    "The agent would lose this key: bind another first.",
                    True,
                )
            )
    if agent.sandbox_id:
        entries = await SandboxCatalogue(db_mgr).entries(org_id, teams=None)
        entry = next((e for e in entries if e.id == agent.sandbox_id), None)
        if entry is not None and entry.team_id and dest != Owner("team", team_id=entry.team_id):
            problems.append(
                MoveProblem(
                    "sandbox",
                    entry.id,
                    entry.name,
                    "A team's sandbox type serves that team's agents only.",
                    True,
                )
            )
    for kind, ids in (("skill", agent.skill_ids), ("knowledge_base", agent.knowledge_base_ids)):
        for object_id in ids:
            try:
                owner, name, _ = await load_owner(
                    app, org_id, Viewer("", is_org_admin=True), kind, object_id
                )
            except MoveError:
                continue
            if _wider(dest, owner):
                problems.append(
                    MoveProblem(
                        kind, object_id, name, "Some of the agent's new users cannot use it.", False
                    )
                )
    return problems


async def _agents_of(app: Any, org_id: str, viewer: Viewer) -> list[AgentPersonaModel]:
    return await listed_agents(
        getattr(app.state, "db_manager", None), org_id, _all_agents_view(viewer)
    )


async def _credential_problems(
    app: Any, org_id: str, viewer: Viewer, item: StoredCredential, dest: Owner
) -> list[MoveProblem]:
    problems: list[MoveProblem] = []
    for agent in await _agents_of(app, org_id, viewer):
        if not any(b.credential_id == item.id for b in agent.credential_bindings):
            continue
        if not _key_serves(dest, owner_of(agent), agent.id, item):
            problems.append(
                MoveProblem(
                    "agent",
                    agent.id,
                    agent.name,
                    "This agent is bound to the key and would lose it.",
                    True,
                )
            )
    return problems


async def _sandbox_problems(
    app: Any, org_id: str, viewer: Viewer, entry: SandboxEntry, dest: Owner
) -> list[MoveProblem]:
    if dest.scope == "org":
        return []
    problems = [
        MoveProblem(
            "agent", a.id, a.name, "This agent starts in the type and is not the team's.", True
        )
        for a in await _agents_of(app, org_id, viewer)
        if a.sandbox_id == entry.id and owner_of(a) != dest
    ]
    problems += [
        MoveProblem(
            "mission", m.id, m.name, "This mission starts in the type and is not the team's.", True
        )
        for m in await mission_store(app).all_for(org_id)
        if m.sandbox_type == entry.id and owner_of(m) != dest
    ]
    return problems


async def _referenced_problems(
    app: Any, org_id: str, viewer: Viewer, kind: str, object_id: str, dest: Owner
) -> list[MoveProblem]:
    """A skill or knowledge base moved to a narrower owner: the agents that name it and reach
    more people."""
    field_name = "skill_ids" if kind == "skill" else "knowledge_base_ids"
    return [
        MoveProblem(
            "agent", a.id, a.name, "Some of this agent's users could no longer use it.", False
        )
        for a in await _agents_of(app, org_id, viewer)
        if object_id in getattr(a, field_name) and _wider(owner_of(a), dest)
    ]


async def _mission_problems(app: Any, org_id: str, mission: Any, dest: Owner) -> list[MoveProblem]:
    if not mission.agent_id:
        return []
    db_mgr = getattr(app.state, "db_manager", None)
    # The mission's owner sees their own personal agent; an administrator's view sees the rest.
    agent = await find_agent(
        db_mgr, org_id, Viewer(mission.user_id or "", is_org_admin=True), mission.agent_id
    )
    if agent is None:
        return []
    runs_as = await mission_principal(db_mgr, org_id, dest.scope, dest.user_id, dest.team_id, "")
    if await may_use(db_mgr, org_id, runs_as, agent):
        return []
    return [
        MoveProblem(
            "agent", agent.id, agent.name, "The mission's runs could no longer use its agent.", True
        )
    ]


async def _problems(
    app: Any, org_id: str, viewer: Viewer, kind: str, object_id: str, record: Any, dest: Owner
) -> list[MoveProblem]:
    if kind == "agent":
        return await _agent_problems(app, org_id, record, dest)
    if kind == "credential":
        return await _credential_problems(app, org_id, viewer, record, dest)
    if kind == "sandbox":
        return await _sandbox_problems(app, org_id, viewer, record, dest)
    if kind in ("skill", "knowledge_base"):
        return await _referenced_problems(app, org_id, viewer, kind, object_id, dest)
    if kind == "mission":
        return await _mission_problems(app, org_id, record, dest)
    return []


async def preview_move(
    app: Any, org_id: str, viewer: Viewer, kind: str, object_id: str, dest: Owner
) -> MovePreview:
    """Who would use the object at ``dest``, whether the caller may move it there, and what
    would break or narrow."""
    if kind not in MOVABLE_KINDS:
        raise MoveError(f"Unknown kind '{kind}'.")
    current, _, record = await load_owner(app, org_id, viewer, kind, object_id)
    db_mgr = getattr(app.state, "db_manager", None)
    who = await audience(db_mgr, org_id, dest)
    if kind == "sandbox" and dest.scope == "user":
        return MovePreview(who, False, "A sandbox type belongs to a team or the organization.")
    if not can_move(viewer, current, dest):
        return MovePreview(who, False, "You need to manage it where it is and where it goes.")
    problems = await _problems(app, org_id, viewer, kind, object_id, record, dest)
    breaking = [p for p in problems if p.breaks]
    reason = (
        f"{len(breaking)} dependenc{'y' if len(breaking) == 1 else 'ies'} would break."
        if breaking
        else ""
    )
    return MovePreview(who, not breaking, reason, problems)
