"""Per-organization subagent roles.

The built-in roles are the defaults; an organization administrator can override or add roles,
stored per organization. A turn resolves its organization's roles once and carries them on its
callbacks, so one organization's edits never change another's delegates.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import replace
from typing import Any

from agent_core.subagents import DEFAULT_SUBAGENT_CONFIGS
from sqlalchemy import select

from api.db.models import SubagentConfigEntityModel
from api.models import SubagentConfigModel
from specifications.interfaces.agent import SubagentConfig

# Development-only store used without a database: org id -> role id -> config.
_memory: dict[str, dict[str, SubagentConfigModel]] = {}


ROLE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def reset_memory_subagents() -> None:
    _memory.clear()


def _row_prefix(org_id: str) -> str:
    """The table's primary key is global, so row ids embed a digest of the owning organization."""
    return hashlib.sha256(org_id.encode()).hexdigest()[:12] + ":"


def _to_model(config: SubagentConfig) -> SubagentConfigModel:
    return SubagentConfigModel(
        id=config.id,
        name=config.name,
        role_title=config.role_title,
        description=config.description,
        system_prompt=config.system_prompt,
        model=config.model,
        max_turns=config.max_turns,
        whitelisted_tools=list(config.whitelisted_tools),
        temperature=config.temperature,
        enabled=config.enabled,
    )


def _to_config(model: SubagentConfigModel) -> SubagentConfig:
    base = DEFAULT_SUBAGENT_CONFIGS.get(model.id)
    config = SubagentConfig(
        id=model.id,
        name=model.name,
        role_title=model.role_title,
        description=model.description,
        system_prompt=model.system_prompt,
        model=model.model,
        max_turns=model.max_turns,
        whitelisted_tools=list(model.whitelisted_tools),
        temperature=model.temperature,
        enabled=model.enabled,
    )
    # Delegation rights are never configurable from the API.
    return replace(config, can_delegate=False, can_ask_user=base.can_ask_user if base else False)


async def _overrides(db_mgr: Any, org_id: str) -> dict[str, SubagentConfigModel]:
    if db_mgr is None:
        return dict(_memory.get(org_id, {}))
    async with db_mgr.session(tenant_org_id=org_id) as session:
        rows = await session.execute(
            select(SubagentConfigEntityModel).where(SubagentConfigEntityModel.org_id == org_id)
        )
        prefix = _row_prefix(org_id)
        return {
            row.id.removeprefix(prefix): SubagentConfigModel(
                id=row.id.removeprefix(prefix),
                name=row.name,
                role_title=row.role_title,
                description=row.description,
                system_prompt=row.system_prompt,
                model=row.model,
                max_turns=row.max_turns,
                whitelisted_tools=list(row.whitelisted_tools or ["*"]),
                temperature=row.temperature,
                enabled=row.enabled,
            )
            for row in rows.scalars().all()
        }


async def load_subagent_configs(db_mgr: Any, org_id: str) -> dict[str, SubagentConfig]:
    """The roles a turn of this organization may delegate to."""
    configs = dict(DEFAULT_SUBAGENT_CONFIGS)
    for role_id, model in (await _overrides(db_mgr, org_id)).items():
        configs[role_id] = _to_config(model)
    return configs


async def list_subagent_configs(db_mgr: Any, org_id: str) -> list[SubagentConfigModel]:
    overrides = await _overrides(db_mgr, org_id)
    configs = dict(DEFAULT_SUBAGENT_CONFIGS)
    for role_id, model in overrides.items():
        configs[role_id] = _to_config(model)
    result: list[SubagentConfigModel] = []
    for c in configs.values():
        m = _to_model(c)
        m.built_in = c.id in DEFAULT_SUBAGENT_CONFIGS
        m.customized = c.id in overrides
        result.append(m)
    return result


async def delete_subagent_config(
    db_mgr: Any, org_id: str, subagent_id: str, disable: bool = False
) -> tuple[bool, bool]:
    """Delete this organization's override or custom subagent role, or disable a built-in role.

    Returns (deleted, is_reset_to_default).
    """
    is_builtin = subagent_id in DEFAULT_SUBAGENT_CONFIGS
    if is_builtin and disable:
        base = DEFAULT_SUBAGENT_CONFIGS[subagent_id]
        disabled_model = _to_model(replace(base, enabled=False))
        await save_subagent_config(db_mgr, org_id, disabled_model)
        return True, False
    if db_mgr is None:
        if org_id in _memory and subagent_id in _memory[org_id]:
            del _memory[org_id][subagent_id]
            return True, is_builtin
        return False, False
    async with db_mgr.session(tenant_org_id=org_id) as session:
        row = (
            (
                await session.execute(
                    select(SubagentConfigEntityModel).where(
                        SubagentConfigEntityModel.id == _row_prefix(org_id) + subagent_id,
                        SubagentConfigEntityModel.org_id == org_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is not None:
            await session.delete(row)
            return True, is_builtin
        return False, False


async def save_subagent_config(
    db_mgr: Any, org_id: str, config: SubagentConfigModel
) -> SubagentConfigModel:
    """Create or replace this organization's role ``config.id``."""
    if db_mgr is None:
        _memory.setdefault(org_id, {})[config.id] = config
        return config
    now = int(time.time())
    async with db_mgr.session(tenant_org_id=org_id) as session:
        row = (
            (
                await session.execute(
                    select(SubagentConfigEntityModel).where(
                        SubagentConfigEntityModel.id == _row_prefix(org_id) + config.id,
                        SubagentConfigEntityModel.org_id == org_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        fields = {
            "name": config.name,
            "role_title": config.role_title,
            "description": config.description,
            "system_prompt": config.system_prompt,
            "model": config.model,
            "max_turns": config.max_turns,
            "whitelisted_tools": config.whitelisted_tools,
            "temperature": config.temperature,
            "enabled": config.enabled,
            "updated_at": now,
        }
        if row is None:
            session.add(
                SubagentConfigEntityModel(
                    id=_row_prefix(org_id) + config.id, org_id=org_id, created_at=now, **fields
                )
            )
        else:
            for name, value in fields.items():
                setattr(row, name, value)
    return config
