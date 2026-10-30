"""Finding the custom skills a member may see (in memory or in the database)."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select

from api.db.models import (
    CustomSkillModel,
)
from api.services.settings import effective_settings
from api.services.starter_content import starter_skill

logger = logging.getLogger(__name__)


# ============================================================================
# CUSTOM PYTHON SKILLS REST API
# ============================================================================

# Without a database (development only) skills live in process memory, keyed by organization so
# one tenant can never list or delete another's.
_memory_skills: dict[str, dict[str, dict[str, Any]]] = {}


@dataclass(frozen=True)
class _VisibleSkill:
    id: str
    runtime: str
    code: str
    body: str


async def _find_visible_skill(
    db_mgr: Any, org_id: str, user_id: str, name: str, allowed_ids: Sequence[str] = ()
) -> _VisibleSkill | None:
    """The org-wide skill, or the caller's own, with this name (never another member's).

    A persona that lists skills may run only those; "not attached" looks exactly like
    "does not exist" so a restricted persona cannot be used to probe which skills exist.
    """
    skill = await _lookup_visible_skill(db_mgr, org_id, user_id, name)
    if skill is None:
        skill = await _starter(db_mgr, org_id, name)
    if skill is not None and allowed_ids and skill.id not in allowed_ids:
        return None
    return skill


def starter_id(name: str) -> str:
    """The id a starter skill is listed and attached under (no row exists for it)."""
    return f"starter:{name}"


async def _enabled_starters(db_mgr: Any, org_id: str) -> list[str]:
    general = await effective_settings(db_mgr, org_id, "general")
    return [str(name) for name in general.get("starter_skills") or []]


async def _starter(db_mgr: Any, org_id: str, name: str) -> _VisibleSkill | None:
    found = starter_skill(name, await _enabled_starters(db_mgr, org_id))
    if found is None:
        return None
    return _VisibleSkill(starter_id(found.name), "text", "", found.body)


async def _lookup_visible_skill(
    db_mgr: Any, org_id: str, user_id: str, name: str
) -> _VisibleSkill | None:
    if db_mgr is not None:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            result = await session.execute(
                select(CustomSkillModel).where(
                    CustomSkillModel.org_id == org_id,
                    CustomSkillModel.name == name,
                    CustomSkillModel.is_active.is_(True),
                    or_(CustomSkillModel.scope == "org", CustomSkillModel.user_id == user_id),
                )
            )
            row = result.scalars().first()
            if row is None:
                return None
            return _VisibleSkill(row.id, row.runtime or "text", row.code or "", row.body or "")
    for skill_id, record in _memory_skills.get(org_id, {}).items():
        if (
            record["name"] == name
            and record.get("is_active", True)
            and (record.get("scope") == "org" or record.get("user_id") == user_id)
        ):
            return _VisibleSkill(
                skill_id,
                record.get("runtime", "text"),
                record.get("code", ""),
                record.get("body", ""),
            )
    return None


async def list_visible_skills(
    db_mgr: Any, org_id: str, user_id: str, allowed_ids: Sequence[str] = ()
) -> list[tuple[str, str]]:
    """``(name, description)`` of every skill the member may use, in name order.

    Same visibility as ``_find_visible_skill``: organization skills and the caller's own, narrowed
    to the persona's list when it has one. The agent can only run what it knows by name.
    """
    found: dict[str, tuple[str, str]] = {}
    if db_mgr is not None:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            result = await session.execute(
                select(CustomSkillModel).where(
                    CustomSkillModel.org_id == org_id,
                    CustomSkillModel.is_active.is_(True),
                    or_(CustomSkillModel.scope == "org", CustomSkillModel.user_id == user_id),
                )
            )
            for row in result.scalars():
                found[row.id] = (row.name, row.description or "")
    else:
        for skill_id, record in _memory_skills.get(org_id, {}).items():
            if record.get("is_active", True) and (
                record.get("scope") == "org" or record.get("user_id") == user_id
            ):
                found[skill_id] = (record["name"], record.get("description", ""))
    taken = {name for name, _ in found.values()}
    for name in await _enabled_starters(db_mgr, org_id):
        starter = starter_skill(name, (name,))
        if starter is not None and name not in taken:
            found[starter_id(name)] = (name, starter.description)
    return sorted(
        (entry for skill_id, entry in found.items() if not allowed_ids or skill_id in allowed_ids)
    )
