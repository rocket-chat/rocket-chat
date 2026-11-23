"""Reading and writing custom skills outside a request: the database, or process memory in
development without one. The skills router keeps its own request-shaped paths; importing from
an external source and synchronising one go through here."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from api.db.models import CustomSkillModel
from api.services.ownership import owner_columns, owner_of
from api.services.skills_lookup import _memory_skills
from api.services.teams import note_memory_owned
from specifications.interfaces.ownership import Owner


@dataclass(frozen=True)
class SkillContent:
    name: str
    description: str
    body: str


@dataclass(frozen=True)
class StoredSkill:
    id: str
    name: str
    description: str
    body: str
    owner: Owner
    is_active: bool


def _from_record(record: Any) -> StoredSkill:
    def field(name: str, default: Any) -> Any:
        value = record.get(name) if isinstance(record, dict) else getattr(record, name, None)
        return default if value is None else value

    return StoredSkill(
        id=str(field("id", "")),
        name=str(field("name", "")),
        description=str(field("description", "")),
        body=str(field("body", "")),
        owner=owner_of(record),
        is_active=bool(field("is_active", True)),
    )


def _owned_by(record: Any, owner: Owner) -> bool:
    return owner_of(record) == owner


async def get_skill(db_mgr: Any, org_id: str, skill_id: str) -> StoredSkill | None:
    if db_mgr is None:
        found = _memory_skills.get(org_id, {}).get(skill_id)
        return _from_record(found) if found is not None else None
    async with db_mgr.session(tenant_org_id=org_id) as db:
        row = await db.get(CustomSkillModel, skill_id)
        return _from_record(row) if row is not None and row.org_id == org_id else None


async def skills_named(
    db_mgr: Any, org_id: str, owner: Owner, names: list[str]
) -> dict[str, StoredSkill]:
    """The skills of ``owner`` with one of ``names``, by name."""
    wanted = set(names)
    if db_mgr is None:
        records = _memory_skills.get(org_id, {}).values()
        return {
            str(r["name"]): _from_record(r)
            for r in records
            if r.get("name") in wanted and _owned_by(r, owner)
        }
    statement = select(CustomSkillModel).where(
        CustomSkillModel.org_id == org_id,
        CustomSkillModel.name.in_(sorted(wanted)),
        CustomSkillModel.scope == owner.scope,
        CustomSkillModel.user_id == owner.user_id,
        CustomSkillModel.team_id == owner.team_id,
    )
    async with db_mgr.session(tenant_org_id=org_id) as db:
        rows = (await db.execute(statement)).scalars().all()
        return {row.name: _from_record(row) for row in rows}


async def save_skill(
    db_mgr: Any,
    org_id: str,
    owner: Owner,
    content: SkillContent,
    *,
    skill_id: str | None = None,
    is_active: bool = True,
) -> StoredSkill:
    """Create a prompt skill for ``owner``, or replace the content of ``skill_id`` (its owner
    is kept)."""
    now = int(time.time())
    target_id = skill_id or f"skill-{uuid.uuid4().hex[:8]}"
    if db_mgr is None:
        return _save_in_memory(org_id, owner, content, target_id, is_active, now)
    async with db_mgr.session(tenant_org_id=org_id) as db:
        row = await db.get(CustomSkillModel, target_id) if skill_id else None
        if row is None or row.org_id != org_id:
            row = CustomSkillModel(
                id=target_id, org_id=org_id, runtime="text", created_at=now, **owner_columns(owner)
            )
            db.add(row)
        row.name, row.description, row.body = content.name, content.description, content.body
        row.is_active, row.updated_at = is_active, now
        return _from_record(row)


def _save_in_memory(
    org_id: str, owner: Owner, content: SkillContent, skill_id: str, is_active: bool, now: int
) -> StoredSkill:
    bucket = _memory_skills.setdefault(org_id, {})
    current = bucket.get(skill_id)
    if current is None:
        if owner.team_id:
            note_memory_owned(org_id, owner.team_id, "skills", 1)
        current = {"id": skill_id, "runtime": "text", "created_at": now, **owner_columns(owner)}
        bucket[skill_id] = current
    current.update(
        name=content.name,
        title=content.name,
        description=content.description,
        body=content.body,
        is_active=is_active,
        updated_at=now,
    )
    return _from_record(current)


async def set_active(db_mgr: Any, org_id: str, skill_id: str, is_active: bool) -> None:
    now = int(time.time())
    if db_mgr is None:
        record = _memory_skills.get(org_id, {}).get(skill_id)
        if record is not None:
            record["is_active"], record["updated_at"] = is_active, now
        return
    async with db_mgr.session(tenant_org_id=org_id) as db:
        row = await db.get(CustomSkillModel, skill_id)
        if row is not None and row.org_id == org_id:
            row.is_active, row.updated_at = is_active, now
