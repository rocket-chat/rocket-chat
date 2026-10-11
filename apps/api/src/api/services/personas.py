"""Agent persona storage and visibility.

Built-in personas are immutable code. Custom personas belong to one organization and are
either organization-wide (``scope="org"``) or personal; they are always read from the store
(no process-wide cache), so one tenant's persona can never be seen, resolved or shadowed by
another's, and edits are visible on every replica immediately. Without a database
(development only) they live in per-organization memory.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from api.db.models import AgentPersonaEntityModel
from api.models import AgentPersonaModel

DEFAULT_AGENT_PERSONAS: tuple[AgentPersonaModel, ...] = (
    AgentPersonaModel(
        id="persona-general",
        name="General Software Engineer",
        role_title="Senior Autonomous Engineer",
        description="Autonomous full-stack engineering agent with full access to terminal execution, file inspection and editing, web lookup, git operations, and interactive decision gates.",
        system_prompt=(
            "You are a Senior Autonomous Software Engineer running inside Rocket Chat. You write clean, modular, production-grade code adhering to strict types and software craftsmanship. "
            "You have access to a dedicated Linux container sandbox mounted at /workspace which is lazily provisioned on-demand. "
            "Plan methodically, inspect files, apply surgical diffs, run terminal commands, and verify your work with automated tests."
        ),
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
        id="persona-security",
        name="Security & Penetration Auditor",
        role_title="Application Security Specialist",
        description="Audits source code for injection attacks, RLS boundary leaks, secret exposure, and permissions.",
        system_prompt=(
            "You are a dedicated Security Auditor persona. Inspect source files and configuration for "
            "vulnerabilities (OWASP Top 10, SQL injection, RLS bypasses, secret leakage)."
        ),
        model="openrouter/anthropic/claude-3.5-sonnet",
        whitelisted_tools=["file_read", "web_search", "web_fetch"],
        icon="shield",
    ),
)

DEFAULT_AGENT_PERSONAS = tuple(
    p.model_copy(update={"built_in": True}) for p in DEFAULT_AGENT_PERSONAS
)
RESERVED_IDS = frozenset(p.id for p in DEFAULT_AGENT_PERSONAS)

# Development-only store used when no database is configured: org id -> persona id -> persona.
_memory: dict[str, dict[str, AgentPersonaModel]] = {}


def reset_memory_personas() -> None:
    _memory.clear()


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
        sandbox_image=entity.sandbox_image,
        cpu_limit=entity.cpu_limit,
        memory_limit=entity.memory_limit,
        temperature=entity.temperature,
        thinking_budget_tokens=entity.thinking_budget_tokens,
        reasoning_effort=entity.reasoning_effort,
        max_turns=entity.max_turns,
        scope=entity.scope,
        user_id=entity.user_id,
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
    "sandbox_image",
    "cpu_limit",
    "memory_limit",
    "temperature",
    "thinking_budget_tokens",
    "reasoning_effort",
    "max_turns",
    "scope",
)


def _visible(scope: str, owner: str | None, user_id: str) -> bool:
    return scope == "org" or owner == user_id


async def list_personas(db_mgr: Any, org_id: str, user_id: str) -> list[AgentPersonaModel]:
    """Built-in personas plus the organization's and the member's own."""
    custom: list[AgentPersonaModel]
    if db_mgr is not None:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            rows = await session.execute(
                select(AgentPersonaEntityModel).where(
                    AgentPersonaEntityModel.org_id == org_id,
                    or_(
                        AgentPersonaEntityModel.scope == "org",
                        AgentPersonaEntityModel.user_id == user_id,
                    ),
                )
            )
            custom = [_from_entity(row) for row in rows.scalars().all()]
    else:
        custom = [
            p for p in _memory.get(org_id, {}).values() if _visible(p.scope, p.user_id, user_id)
        ]
    return [*DEFAULT_AGENT_PERSONAS, *custom]


async def get_persona(
    db_mgr: Any, org_id: str, user_id: str, persona_id: str | None
) -> AgentPersonaModel | None:
    """The persona ``persona_id`` as visible to this member, or ``None``."""
    if not persona_id:
        return None
    for default in DEFAULT_AGENT_PERSONAS:
        if default.id == persona_id:
            return default
    if db_mgr is not None:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            entity = (
                (
                    await session.execute(
                        select(AgentPersonaEntityModel).where(
                            AgentPersonaEntityModel.id == persona_id,
                            AgentPersonaEntityModel.org_id == org_id,
                            or_(
                                AgentPersonaEntityModel.scope == "org",
                                AgentPersonaEntityModel.user_id == user_id,
                            ),
                        )
                    )
                )
                .scalars()
                .first()
            )
            return _from_entity(entity) if entity is not None else None
    found = _memory.get(org_id, {}).get(persona_id)
    return found if found is not None and _visible(found.scope, found.user_id, user_id) else None


def _check_may_write(scope: str, owner: str | None, user_id: str, is_admin: bool) -> None:
    if scope == "org" and not is_admin:
        raise PersonaError(
            403, "Only Organization Administrators can change organization personas."
        )
    if scope == "user" and owner != user_id and not is_admin:
        raise PersonaError(403, "Forbidden")


async def save_persona(
    db_mgr: Any, org_id: str, user_id: str, is_admin: bool, persona: AgentPersonaModel
) -> AgentPersonaModel:
    """Create or update a custom persona; the stored id is returned on the result.

    The client proposes an id, but ids are global primary keys: a taken or reserved id is
    replaced by a generated one, so a tenant can neither overwrite a built-in nor collide
    with (and thereby probe) another tenant's persona.
    """
    if persona.id in RESERVED_IDS:
        raise PersonaError(409, f"'{persona.id}' is a built-in persona and cannot be modified.")
    if persona.scope == "org" and not is_admin:
        raise PersonaError(
            403, "Only Organization Administrators can configure organization-wide agent personas."
        )
    persona.user_id = user_id if persona.scope == "user" else None
    persona.built_in = False
    now = int(time.time())

    if db_mgr is None:
        bucket = _memory.setdefault(org_id, {})
        existing = bucket.get(persona.id)
        if existing is not None:
            _check_may_write(existing.scope, existing.user_id, user_id, is_admin)
        bucket[persona.id] = persona
        return persona

    async with db_mgr.session(tenant_org_id=org_id) as session:
        entity = (
            (
                await session.execute(
                    select(AgentPersonaEntityModel).where(
                        AgentPersonaEntityModel.id == persona.id,
                        AgentPersonaEntityModel.org_id == org_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if entity is not None:
            _check_may_write(entity.scope, entity.user_id, user_id, is_admin)
            for name in _COPIED_FIELDS:
                setattr(entity, name, getattr(persona, name))
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
                            **{name: getattr(candidate, name) for name in _COPIED_FIELDS},
                        )
                    )
            except IntegrityError:
                continue
            await session.commit()
            return candidate
    raise PersonaError(409, "Could not allocate a persona id; retry.")


async def delete_persona(
    db_mgr: Any, org_id: str, user_id: str, is_admin: bool, persona_id: str
) -> None:
    if persona_id in RESERVED_IDS:
        raise PersonaError(409, "Built-in personas cannot be deleted.")
    if db_mgr is None:
        existing = _memory.get(org_id, {}).get(persona_id)
        if existing is None:
            raise PersonaError(404, "Agent persona not found")
        _check_may_write(existing.scope, existing.user_id, user_id, is_admin)
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
        _check_may_write(entity.scope, entity.user_id, user_id, is_admin)
        await session.delete(entity)
        await session.commit()
