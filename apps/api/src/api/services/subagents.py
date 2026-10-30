"""Per-organization subagent roles.

The built-in roles are the defaults; an organization administrator can override or add roles,
stored per organization. A turn resolves its organization's roles once and carries them on its
callbacks, so one organization's edits never change another's delegates.
"""

from __future__ import annotations

import copy
import hashlib
import re
import time
from dataclasses import replace
from typing import Any

from agent_core.subagents import DEFAULT_SUBAGENT_CONFIGS
from sqlalchemy import select

from api.db.models import SubagentConfigEntityModel
from api.models import SubagentConfigModel
from specifications.interfaces.agent import DEFAULT_SUBAGENT_INHERIT, SubagentConfig

# Development-only store used without a database: org id -> role id -> config.
_memory: dict[str, dict[str, SubagentConfigModel]] = {}


ROLE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def reset_memory_subagents() -> None:
    _memory.clear()


def _row_prefix(org_id: str) -> str:
    """The table's primary key is global, so row ids embed a digest of the owning organization."""
    return hashlib.sha256(org_id.encode()).hexdigest()[:12] + ":"


# The fields an organization stores for a role (the API model, the row and the config share them).
_STORED_FIELDS = (
    "name", "role_title", "description", "system_prompt", "model", "max_turns",
    "whitelisted_tools", "disallowed_tools", "temperature", "enabled", "tier",
    "reasoning_effort", "max_cost_usd", "max_wall_seconds", "isolation", "can_ask_user",
    "inherit", "output_schema",
)  # fmt: skip


def _to_model(config: SubagentConfig) -> SubagentConfigModel:
    return SubagentConfigModel(
        id=config.id,
        source="builtin" if config.source == "builtin" else "org",
        **{name: copy.deepcopy(getattr(config, name)) for name in _STORED_FIELDS},
    )


def _to_config(model: SubagentConfigModel) -> SubagentConfig:
    values = {name: copy.deepcopy(getattr(model, name)) for name in _STORED_FIELDS}
    # Delegation rights are never configurable from the API.
    return SubagentConfig(id=model.id, source="org", can_delegate=False, **values)


def _row_values(row: SubagentConfigEntityModel) -> dict[str, Any]:
    values = {name: getattr(row, name) for name in _STORED_FIELDS}
    # Columns added after a row was written read as their defaults.
    values["whitelisted_tools"] = list(row.whitelisted_tools or ["*"])
    values["disallowed_tools"] = list(row.disallowed_tools or [])
    values["inherit"] = (
        list(row.inherit) if row.inherit is not None else list(DEFAULT_SUBAGENT_INHERIT)
    )
    values["tier"] = row.tier or "default"
    values["isolation"] = row.isolation or "none"
    return values


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
                id=row.id.removeprefix(prefix), **_row_values(row)
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
        fields = {name: getattr(config, name) for name in _STORED_FIELDS}
        fields["updated_at"] = now
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
