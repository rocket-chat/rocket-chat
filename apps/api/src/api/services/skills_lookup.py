"""Finding the custom skills a member may see (in memory or in the database)."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select

from api.db.models import (
    CustomSkillModel,
)

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
    if skill is not None and allowed_ids and skill.id not in allowed_ids:
        return None
    return skill


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
