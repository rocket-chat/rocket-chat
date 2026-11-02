"""Agent persona storage and visibility.

Built-in personas are immutable code. Custom personas belong to one organization and are
owned by the organization (``scope="org"``), a team or a member; they are always read from the store
(no process-wide cache), so one tenant's persona can never be seen, resolved or shadowed by
another's, and edits are visible on every replica immediately. Without a database
(development only) they live in per-organization memory.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from api.auth.capabilities import Capability, member_has
from api.db.models import AgentPersonaEntityModel
from api.models import AgentPersonaModel, CredentialBindingModel
from api.services.agent_access import AgentAccessDenied, may_use, principal_for
from api.services.model_credentials import ModelCredentialStore
from api.services.ownership import (
    OwnershipError,
    manageable,
    owner_for_write,
    owner_of,
    visible,
    visible_clause,
)
from api.services.sandbox_catalogue import SandboxCatalogue
from api.services.settings import effective_settings, resolve_settings
from api.services.teams import note_memory_owned
from api.services.turn_limits import organization_max_turns
from specifications.interfaces.agent import NO_STEP_LIMIT
from specifications.interfaces.ownership import Owner, Viewer

# The default agent's prompt, also used when a session's agent has none. Model-agnostic: the
# environment (sandbox, tools, git) is described by the platform's own system prompt sections.
DEFAULT_AGENT_PROMPT = (
    "You are a versatile senior software engineer working in Rocket Chat. You take on any "
    "engineering task: answer questions, investigate, design, write and change code, review, "
    "debug and operate. You work with a terminal, surgical file edits and patches, web "
    "lookups and a task checklist. Read before you change anything, keep changes minimal and "
    "in the style of the code around them, verify them before you say they work, and say "
    "plainly what you did not do or could not check."
)

# Inspection only, as for the read-only subagent roles.
_READ_ONLY_TOOLS = [
    "file_read", "grep_search", "glob_files", "list_dir", "code_search", "web_search", "web_fetch",
]  # fmt: skip

# What a reviewer reads a change with, besides the files: git history and the pull request.
_REVIEW_TOOLS = [
    "git_diff", "git_log", "git_status", "clone_repository", "set_active_repository",
    "github_pull_request", "github_read_file", "github_search",
]  # fmt: skip

DEFAULT_AGENT_PERSONAS: tuple[AgentPersonaModel, ...] = (
    AgentPersonaModel(
        id="persona-general",
        name="General Software Engineer",
        role_title="Senior Software Engineer",
        description="Autonomous full-stack engineering agent with full access to terminal execution, file inspection and editing, web lookup, git operations, and interactive decision gates.",
        system_prompt=DEFAULT_AGENT_PROMPT,
        # Empty model: inherit the member's preference / the organization default.
        model="",
        # Wildcard: the versatile default agent gets every tool the runtime exposes
        # (sandbox/repo, memory & knowledge, web, MCP connectors, enabled toolsets), and
        # any tool added later is picked up automatically. A restricted persona lists the
        # exact tools it may use instead.
        whitelisted_tools=["*"],
        icon="bot",
    ),
    AgentPersonaModel(
        id="persona-backend",
        name="Backend Developer",
        role_title="Senior Backend & Systems Engineer",
        description="Autonomous backend engineer specialized in distributed systems, APIs, database migrations, microservices, and durable multi-turn execution.",
        system_prompt=(
            "You are a Senior Backend & Systems Engineer working in Rocket Chat. You specialize in "
            "reliable backend development, API design, database migrations, asynchronous pipelines, "
            "and microservice refactors. You write idiomatic, type-safe, well-tested code. "
            "Build and test in the sandbox before you push, then use wait_for on the pull "
            "request's CI."
        ),
        model="",
        reasoning_effort="medium",
        max_turns=100,
        whitelisted_tools=["*"],
        # The core tools are always sent; these are the few more a backend task keeps using.
        active_tools=["process_start", "process_output", "process_wait", "git_diff"],
        icon="server",
    ),
    AgentPersonaModel(
        id="persona-reviewer",
        name="Reviewer",
        role_title="Code Reviewer",
        description="Reviews a change or a pull request: correctness, edge cases, tests and readability, with findings ranked by severity. Changes nothing.",
        system_prompt=(
            "You are a code reviewer. Review the change you are pointed at (the working diff, a "
            "branch or a pull request), not the whole codebase. Read the diff, then the code "
            "around each change and its tests. Report each problem with its file and line, why "
            "it is wrong and what to do instead, ranked: blocking (bugs, data loss, security, "
            "broken contracts), then should-fix (missing tests, unhandled cases), then nits. Say "
            "what you checked and found fine. Never change files or push."
        ),
        model="",
        reasoning_effort="medium",
        whitelisted_tools=[*_READ_ONLY_TOOLS, *_REVIEW_TOOLS],
        icon="search-check",
    ),
    AgentPersonaModel(
        id="persona-architect",
        name="Architect",
        role_title="Software Architect",
        description="Designs before building: investigates in plan mode, proposes a plan for approval, then carries it out. Runs on the frontier model.",
        system_prompt=(
            "You are a software architect. For any change beyond a one-liner, investigate first: "
            "read the code, its tests and its documentation, and find the constraints. Then "
            "propose a plan as a checklist, with the trade-offs you weighed and the risks, and "
            "ask for approval before you change anything. Once approved, carry it out step by "
            "step, verifying each step."
        ),
        # The organization's frontier model, whichever provider it has a key for.
        model="tier:frontier",
        reasoning_effort="high",
        default_permission_mode="plan",
        whitelisted_tools=["*"],
        icon="compass",
    ),
    AgentPersonaModel(
        id="persona-security",
        name="Security Auditor",
        role_title="Application Security Specialist",
        description="Audits code for vulnerabilities: entry points, untrusted input, secrets, access control. Reports findings by severity with evidence and fixes; changes nothing.",
        system_prompt=(
            "You are a security auditor. Work in this order: (1) a short threat model: what the "
            "code protects and from whom; (2) the entry points: requests, messages, files, "
            "commands, anything an outsider controls; (3) the data flow from each entry point to "
            "where it is used (queries, shell, file paths, templates, network calls, "
            "authorization checks); (4) what can go wrong on each flow (injection, broken access "
            "control, secrets in code or logs, unsafe deserialization, requests to "
            "attacker-chosen hosts). Rank findings: critical (exploitable now), high (with "
            "little effort), medium (needs unusual conditions), low (hardening). Give each the "
            "file, line, evidence and a fix. Never change files."
        ),
        # Empty model: the member's or organization's choice, whichever provider it is.
        model="",
        whitelisted_tools=[*_READ_ONLY_TOOLS, "git_diff", "git_log", "git_status"],
        icon="shield",
    ),
)

DEFAULT_AGENT_PERSONAS = tuple(
    p.model_copy(update={"built_in": True}) for p in DEFAULT_AGENT_PERSONAS
)
RESERVED_IDS = frozenset(p.id for p in DEFAULT_AGENT_PERSONAS)

# Development-only store used when no database is configured: org id -> persona id -> persona.
_memory: dict[str, dict[str, AgentPersonaModel]] = {}


# Administrators' edits of built-in personas without a database: org id -> built-in id -> persona.
_memory_overrides: dict[str, dict[str, AgentPersonaModel]] = {}


def reset_memory_personas() -> None:
    _memory.clear()
    _memory_overrides.clear()


def _override_row_id(org_id: str, builtin_id: str) -> str:
    """A stable primary key per (organization, built-in): ids are global, built-in ids are not."""
    return "ovr-" + hashlib.sha256(f"{org_id}:{builtin_id}".encode()).hexdigest()[:24]


def _as_builtin(persona: AgentPersonaModel, builtin_id: str) -> AgentPersonaModel:
    """An organization's edit of a built-in, presented under the built-in's own id."""
    return persona.model_copy(
        update={
            "id": builtin_id,
            "built_in": True,
            "customized": True,
            "scope": "org",
            "user_id": None,
            "team_id": None,
        }
    )


def _apply_overrides(
    overrides: dict[str, AgentPersonaModel],
) -> list[AgentPersonaModel]:
    return [overrides.get(p.id, p) for p in DEFAULT_AGENT_PERSONAS]


@dataclass
class PersonaError(Exception):
    """A rejected persona operation, carrying the HTTP status to report."""

    status_code: int
    detail: str


def _from_entity(entity: AgentPersonaEntityModel) -> AgentPersonaModel:
    return AgentPersonaModel(
        id=entity.id,
        name=entity.name,
        role_title=entity.role_title,
        description=entity.description,
        system_prompt=entity.system_prompt,
        model=entity.model,
        whitelisted_tools=entity.whitelisted_tools or [],
        skill_ids=entity.skill_ids or [],
        knowledge_base_ids=entity.knowledge_base_ids or [],
        icon=entity.icon,
        sandbox_id=entity.sandbox_id or "",
        temperature=entity.temperature,
        reasoning_effort=entity.reasoning_effort,
        credential_bindings=[
            CredentialBindingModel(**binding)
            for binding in entity.credential_bindings or []
            if isinstance(binding, dict)
        ],
        strict_binding=bool(entity.strict_binding),
        max_turns=entity.max_turns,
        active_tools=entity.active_tools or [],
        enabled=getattr(entity, "enabled", True)
        if getattr(entity, "enabled", True) is not None
        else True,
        default_permission_mode=getattr(entity, "default_permission_mode", None),
        scope=entity.scope,
        user_id=entity.user_id,
        team_id=entity.team_id,
    )


_COPIED_FIELDS = (
    "name",
    "role_title",
    "description",
    "system_prompt",
    "model",
    "whitelisted_tools",
    "skill_ids",
    "knowledge_base_ids",
    "icon",
    "sandbox_id",
    "temperature",
    "reasoning_effort",
    "strict_binding",
    "max_turns",
    "active_tools",
    "enabled",
    "default_permission_mode",
    "scope",
    "team_id",
)


def _fields(persona: AgentPersonaModel) -> dict[str, Any]:
    """The stored columns of ``persona``."""
    fields = {name: getattr(persona, name) for name in _COPIED_FIELDS}
    fields["credential_bindings"] = [b.model_dump() for b in persona.credential_bindings]
    return fields


async def list_personas(
    db_mgr: Any, org_id: str, user_id: str, roles: list[str] | None = None
) -> list[AgentPersonaModel]:
    """The agents this member may use: built-in (as the organization edited them),
    the organization's (per their grants), their teams' and their own."""
    principal = await principal_for(db_mgr, org_id, user_id, roles)
    return [
        persona
        for persona in await _listed_personas(db_mgr, org_id, principal.viewer)
        if await may_use(db_mgr, org_id, principal, persona)
        or (principal.is_admin and persona.enabled is False)
    ]


async def _listed_personas(db_mgr: Any, org_id: str, viewer: Viewer) -> list[AgentPersonaModel]:
    custom: list[AgentPersonaModel]
    if db_mgr is not None:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            rows = await session.execute(
                select(AgentPersonaEntityModel).where(
                    AgentPersonaEntityModel.org_id == org_id,
                    visible_clause(AgentPersonaEntityModel, viewer),
                )
            )
            entities = rows.scalars().all()
            custom = [_from_entity(row) for row in entities if not row.overrides_builtin]
            overrides = {
                row.overrides_builtin: _as_builtin(_from_entity(row), row.overrides_builtin)
                for row in entities
                if row.overrides_builtin
            }
    else:
        custom = [p for p in _memory.get(org_id, {}).values() if visible(p, viewer)]
        overrides = dict(_memory_overrides.get(org_id, {}))
    return [*_apply_overrides(overrides), *custom]


async def get_persona(
    db_mgr: Any,
    org_id: str,
    user_id: str,
    persona_id: str | None,
    *,
    roles: list[str] | None = None,
    enforce_access: bool = True,
) -> AgentPersonaModel | None:
    """The persona ``persona_id`` for this member: ``None`` when it does not exist (or is
    another member's), ``AgentAccessDenied`` when it exists but they may not use it.

    ``enforce_access=False`` is for the platform's own use of an agent (a Slack bot's router).
    """
    principal = await principal_for(db_mgr, org_id, user_id, roles)
    persona = await _find_persona(db_mgr, org_id, principal.viewer, persona_id)
    if persona is None or not enforce_access:
        return persona
    if not await may_use(db_mgr, org_id, principal, persona):
        raise AgentAccessDenied(persona.id, persona.name, disabled=persona.enabled is False)
    return persona


async def _find_persona(
    db_mgr: Any, org_id: str, viewer: Viewer, persona_id: str | None
) -> AgentPersonaModel | None:
    if not persona_id:
        return None
    for default in DEFAULT_AGENT_PERSONAS:
        if default.id == persona_id:
            return await _builtin_for_org(db_mgr, org_id, default)
    if db_mgr is not None:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            entity = (
                (
                    await session.execute(
                        select(AgentPersonaEntityModel).where(
                            AgentPersonaEntityModel.id == persona_id,
                            AgentPersonaEntityModel.org_id == org_id,
                            visible_clause(AgentPersonaEntityModel, viewer),
                        )
                    )
                )
                .scalars()
                .first()
            )
            return _from_entity(entity) if entity is not None else None
    found = _memory.get(org_id, {}).get(persona_id)
    return found if found is not None and visible(found, viewer) else None


async def _builtin_for_org(
    db_mgr: Any, org_id: str, default: AgentPersonaModel
) -> AgentPersonaModel:
    if db_mgr is None:
        return _memory_overrides.get(org_id, {}).get(default.id, default)
    async with db_mgr.session(tenant_org_id=org_id) as session:
        entity = (
            (
                await session.execute(
                    select(AgentPersonaEntityModel).where(
                        AgentPersonaEntityModel.org_id == org_id,
                        AgentPersonaEntityModel.overrides_builtin == default.id,
                    )
                )
            )
            .scalars()
            .first()
        )
    return _as_builtin(_from_entity(entity), default.id) if entity is not None else default


async def _save_builtin_override(
    db_mgr: Any, org_id: str, is_admin: bool, persona: AgentPersonaModel
) -> AgentPersonaModel:
    """Store an administrator's edit of a built-in agent for the whole organization."""
    if not is_admin:
        raise PersonaError(
            403,
            "Only Organization Administrators can edit the built-in agents; save a copy instead.",
        )
    edited = _as_builtin(persona, persona.id)
    if db_mgr is None:
        _memory_overrides.setdefault(org_id, {})[persona.id] = edited
        return edited
    now = int(time.time())
    async with db_mgr.session(tenant_org_id=org_id) as session:
        entity = (
            (
                await session.execute(
                    select(AgentPersonaEntityModel).where(
                        AgentPersonaEntityModel.org_id == org_id,
                        AgentPersonaEntityModel.overrides_builtin == persona.id,
                    )
                )
            )
            .scalars()
            .first()
        )
        fields = _fields(edited)
        if entity is None:
            session.add(
                AgentPersonaEntityModel(
                    id=_override_row_id(org_id, persona.id),
                    org_id=org_id,
                    user_id=None,
                    overrides_builtin=persona.id,
                    created_at=now,
                    updated_at=now,
                    **fields,
                )
            )
        else:
            for name, value in fields.items():
                setattr(entity, name, value)
            entity.updated_at = now
        await session.commit()
    return edited


async def _reset_builtin_override(
    db_mgr: Any, org_id: str, is_admin: bool, builtin_id: str
) -> None:
    if not is_admin:
        raise PersonaError(403, "Only Organization Administrators can reset the built-in agents.")
    if db_mgr is None:
        _memory_overrides.get(org_id, {}).pop(builtin_id, None)
        return
    async with db_mgr.session(tenant_org_id=org_id) as session:
        entity = (
            (
                await session.execute(
                    select(AgentPersonaEntityModel).where(
                        AgentPersonaEntityModel.org_id == org_id,
                        AgentPersonaEntityModel.overrides_builtin == builtin_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if entity is not None:
            await session.delete(entity)
            await session.commit()


def _check_may_write(record: Any, viewer: Viewer) -> None:
    if manageable(record, viewer):
        return
    scope = owner_of(record).scope
    if scope == "org":
        raise PersonaError(
            403, "Only Organization Administrators can change organization personas."
        )
    if scope == "team":
        raise PersonaError(403, "Only the team's administrators can change its agents.")
    raise PersonaError(403, "Forbidden")


async def _validate_step_limit(db_mgr: Any, org_id: str, persona: AgentPersonaModel) -> None:
    if persona.max_turns is None:
        return
    ceiling = await organization_max_turns(db_mgr, org_id)
    if ceiling != NO_STEP_LIMIT and (
        persona.max_turns == NO_STEP_LIMIT or persona.max_turns > ceiling
    ):
        raise PersonaError(
            422, f"Max steps per turn cannot exceed the organization's limit of {ceiling}."
        )


async def _validate_sandbox_type(db_mgr: Any, org_id: str, persona: AgentPersonaModel) -> None:
    if not persona.sandbox_id:
        return
    if await SandboxCatalogue(db_mgr).resolve(org_id, sandbox_id=persona.sandbox_id) is None:
        raise PersonaError(422, "That sandbox type is not in the organization's catalogue.")


async def _validate_permission_mode(
    db_mgr: Any, org_id: str, user_id: str, persona: AgentPersonaModel
) -> None:
    mode = persona.default_permission_mode
    if not mode:
        return
    preferences = await resolve_settings(db_mgr, org_id, "user_preferences")
    locked_mode = preferences.effective.get("permission_mode")
    if "permission_mode" in preferences.locked_keys and mode != locked_mode:
        raise PersonaError(422, f"The organization locked the permission mode to '{locked_mode}'.")
    if mode == "bypass" and not await member_has(
        db_mgr, org_id, user_id, Capability.USE_BYPASS_MODE
    ):
        raise PersonaError(422, "Your role does not have the use_bypass_mode capability.")


async def _validate_bindings(
    db_mgr: Any,
    org_id: str,
    user_id: str,
    is_admin: bool,
    persona: AgentPersonaModel,
    credentials: ModelCredentialStore | None,
    kept: frozenset[tuple[str, str]] = frozenset(),
) -> None:
    """Who may bind which credential (``model_credentials`` resolves them at call time):

    * an organization agent: administrators, with organization credentials or member
      credentials whose owner shared them with this agent;
    * a team's agent: its managers, with member credentials whose owner shared them with this
      agent, and organization credentials only when an organization administrator binds them;
    * a personal agent: its owner, with their own credentials only;
    * never a member credential while ``models.member_keys`` is ``off``.

    ``kept`` are the (provider, credential id) pairs already stored on the agent: whoever bound
    them, a team administrator's later edit of the agent keeps them without rebinding them.
    """
    if not persona.credential_bindings:
        return
    org_agent = persona.scope == "org" or persona.id in RESERVED_IDS
    shared_agent = org_agent or persona.scope == "team"
    if org_agent and not is_admin:
        raise PersonaError(403, "Only administrators bind credentials to organization agents.")
    store = credentials or ModelCredentialStore(db_mgr)
    member_keys = str((await effective_settings(db_mgr, org_id, "models")).get("member_keys"))
    providers: set[str] = set()
    for binding in persona.credential_bindings:
        if binding.provider in providers:
            raise PersonaError(422, f"Bind one credential per provider ({binding.provider}).")
        providers.add(binding.provider)
        found = await store.get(org_id, binding.credential_id)
        if found is None or found.provider != binding.provider:
            raise PersonaError(422, f"No {binding.provider} credential {binding.credential_id}.")
        if found.owner_kind == "member":
            if member_keys == "off":
                raise PersonaError(422, "Your organization does not use members' own keys.")
            shared = persona.id in found.shared_with_org_agents
            if shared_agent and not shared:
                raise PersonaError(
                    422, f"The owner of {found.label!r} has not shared it with this agent."
                )
            if not shared_agent and found.owner_user_id != user_id:
                raise PersonaError(403, "A personal agent can only use its owner's credentials.")
        elif not shared_agent:
            raise PersonaError(403, "A personal agent cannot use organization credentials.")
        elif not is_admin and (binding.provider, binding.credential_id) not in kept:
            raise PersonaError(
                403, "Only organization administrators bind organization credentials."
            )


async def _validate(
    db_mgr: Any,
    org_id: str,
    user_id: str,
    is_admin: bool,
    persona: AgentPersonaModel,
    credentials: ModelCredentialStore | None = None,
) -> None:
    await _validate_step_limit(db_mgr, org_id, persona)
    await _validate_sandbox_type(db_mgr, org_id, persona)
    await _validate_permission_mode(db_mgr, org_id, user_id, persona)
    kept = frozenset(await stored_bindings(db_mgr, org_id, persona.id))
    await _validate_bindings(db_mgr, org_id, user_id, is_admin, persona, credentials, kept)


async def stored_bindings(db_mgr: Any, org_id: str, persona_id: str) -> list[tuple[str, str]]:
    """The (provider, credential id) pairs stored on an agent, for auditing a change."""
    if db_mgr is None:
        found = _memory.get(org_id, {}).get(persona_id) or _memory_overrides.get(org_id, {}).get(
            persona_id
        )
        return [(b.provider, b.credential_id) for b in found.credential_bindings] if found else []
    async with db_mgr.session(tenant_org_id=org_id) as session:
        entity = (
            (
                await session.execute(
                    select(AgentPersonaEntityModel).where(
                        AgentPersonaEntityModel.org_id == org_id,
                        or_(
                            AgentPersonaEntityModel.id == persona_id,
                            AgentPersonaEntityModel.overrides_builtin == persona_id,
                        ),
                    )
                )
            )
            .scalars()
            .first()
        )
    if entity is None:
        return []
    return [
        (str(b.get("provider")), str(b.get("credential_id")))
        for b in entity.credential_bindings or []
        if isinstance(b, dict)
    ]


async def _stored_owner(db_mgr: Any, org_id: str, persona_id: str) -> Owner | None:
    """The owner of the stored custom persona ``persona_id`` of this organization, if any."""
    if db_mgr is None:
        found = _memory.get(org_id, {}).get(persona_id)
        return owner_of(found) if found is not None else None
    async with db_mgr.session(tenant_org_id=org_id) as session:
        entity = (
            (
                await session.execute(
                    select(AgentPersonaEntityModel).where(
                        AgentPersonaEntityModel.id == persona_id,
                        AgentPersonaEntityModel.org_id == org_id,
                        AgentPersonaEntityModel.overrides_builtin.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
    return owner_of(entity) if entity is not None else None


async def _writer(db_mgr: Any, org_id: str, user_id: str, is_admin: bool) -> Viewer:
    """The ownership view of a caller known only by id and administrator flag (platform code
    and tests); routes pass the viewer their request proved."""
    principal = await principal_for(
        db_mgr, org_id, user_id, ["admin", "member"] if is_admin else ["member"]
    )
    return principal.viewer


async def save_persona(
    db_mgr: Any,
    org_id: str,
    user_id: str,
    is_admin: bool,
    persona: AgentPersonaModel,
    credentials: ModelCredentialStore | None = None,
    *,
    viewer: Viewer | None = None,
) -> AgentPersonaModel:
    """Create or update a custom persona; the stored id is returned on the result.

    The client proposes an id, but ids are global primary keys: a taken or reserved id is
    replaced by a generated one, so a tenant can neither overwrite a built-in nor collide
    with (and thereby probe) another tenant's persona. The owner is the one the request names
    (moving it needs the right to manage it on both sides), or the stored one when the request
    keeps its scope, so an administrator's edit never takes a member's agent over.
    """
    viewer = viewer or await _writer(db_mgr, org_id, user_id, is_admin)
    if persona.id in RESERVED_IDS:
        await _validate(db_mgr, org_id, user_id, viewer.is_org_admin, persona, credentials)
        return await _save_builtin_override(db_mgr, org_id, viewer.is_org_admin, persona)
    try:
        owner = await owner_for_write(
            db_mgr,
            org_id,
            viewer,
            scope=persona.scope,
            team_id=persona.team_id,
            current=await _stored_owner(db_mgr, org_id, persona.id),
            what="agents",
        )
    except OwnershipError as err:
        raise PersonaError(err.status_code, err.detail) from err
    persona.scope, persona.user_id, persona.team_id = owner.scope, owner.user_id, owner.team_id
    await _validate(db_mgr, org_id, user_id, viewer.is_org_admin, persona, credentials)
    persona.built_in = False
    now = int(time.time())

    if db_mgr is None:
        bucket = _memory.setdefault(org_id, {})
        previous = bucket.get(persona.id)
        if previous is not None and previous.team_id:
            note_memory_owned(org_id, previous.team_id, "agents", -1)
        if persona.team_id:
            note_memory_owned(org_id, persona.team_id, "agents", 1)
        bucket[persona.id] = persona
        return persona

    async with db_mgr.session(tenant_org_id=org_id) as session:
        entity = (
            (
                await session.execute(
                    select(AgentPersonaEntityModel).where(
                        AgentPersonaEntityModel.id == persona.id,
                        AgentPersonaEntityModel.org_id == org_id,
                        # An edit of a built-in is reached through the built-in's id only.
                        AgentPersonaEntityModel.overrides_builtin.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if entity is not None:
            for name, value in _fields(persona).items():
                setattr(entity, name, value)
            entity.user_id = persona.user_id
            entity.updated_at = now
            await session.commit()
            return persona

        for attempt in range(2):
            candidate = (
                persona
                if attempt == 0
                else persona.model_copy(update={"id": f"agent-{uuid.uuid4().hex[:12]}"})
            )
            try:
                async with session.begin_nested():
                    session.add(
                        AgentPersonaEntityModel(
                            id=candidate.id,
                            org_id=org_id,
                            user_id=candidate.user_id,
                            created_at=now,
                            updated_at=now,
                            **_fields(candidate),
                        )
                    )
            except IntegrityError:
                continue
            await session.commit()
            return candidate
    raise PersonaError(409, "Could not allocate a persona id; retry.")


async def delete_persona(
    db_mgr: Any,
    org_id: str,
    user_id: str,
    is_admin: bool,
    persona_id: str,
    disable: bool = False,
    *,
    viewer: Viewer | None = None,
) -> None:
    viewer = viewer or await _writer(db_mgr, org_id, user_id, is_admin)
    is_admin = viewer.is_org_admin
    if persona_id in RESERVED_IDS:
        if disable:
            # Disable the built-in agent for this organization: store an override with enabled=False.
            default = next((p for p in DEFAULT_AGENT_PERSONAS if p.id == persona_id), None)
            if default is not None:
                disabled_persona = default.model_copy(update={"enabled": False})
                await _save_builtin_override(db_mgr, org_id, is_admin, disabled_persona)
                return
        # Deleting a built-in means undoing the organization's edit: the shipped one returns.
        await _reset_builtin_override(db_mgr, org_id, is_admin, persona_id)
        return
    if db_mgr is None:
        existing = _memory.get(org_id, {}).get(persona_id)
        if existing is None:
            raise PersonaError(404, "Agent persona not found")
        _check_may_write(existing, viewer)
        if existing.team_id:
            note_memory_owned(org_id, existing.team_id, "agents", -1)
        del _memory[org_id][persona_id]
        return
    async with db_mgr.session(tenant_org_id=org_id) as session:
        entity = (
            (
                await session.execute(
                    select(AgentPersonaEntityModel).where(
                        AgentPersonaEntityModel.id == persona_id,
                        AgentPersonaEntityModel.org_id == org_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if entity is None:
            raise PersonaError(404, "Agent persona not found")
        _check_may_write(entity, viewer)
        await session.delete(entity)
        await session.commit()
