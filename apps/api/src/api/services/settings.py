"""Settings persistence: the one loader for organization and member settings.

Reads and writes go to PostgreSQL under the tenant's RLS context. Only a deployment without
a database (local development, e2e) uses the process-local store, and that is decided by the
presence of the database - never as a silent fallback for a database error. A failing read
or write raises: serving defaults for a governance policy because the database hiccuped
would be a quiet policy bypass, and reporting a save as successful when it was not is worse.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from config_engine import CascadingSettingsResolver, ResolvedSettings
from config_engine.registry import specs_for
from sqlalchemy import select

from api.db.models import OrgSettingModel, UserSettingModel

_resolver = CascadingSettingsResolver()

# Used only when no database is configured.
_memory_org: dict[tuple[str, str], tuple[dict[str, Any], list[str]]] = {}
_memory_user: dict[tuple[str, str, str], dict[str, Any]] = {}


def _has_database(db_mgr: Any) -> bool:
    return db_mgr is not None and getattr(db_mgr, "session_factory", None) is not None


def reset_memory_settings() -> None:
    """Forget the process-local settings (tests)."""
    _memory_org.clear()
    _memory_user.clear()


async def load_org_config(
    db_mgr: Any, org_id: str, domain: str
) -> tuple[dict[str, Any], list[str]]:
    """The organization's stored config and locked keys for ``domain``."""
    if not _has_database(db_mgr):
        config, locked = _memory_org.get((org_id, domain), ({}, []))
        return dict(config), list(locked)
    async with db_mgr.session(tenant_org_id=org_id) as session:
        record = (
            await session.execute(
                select(OrgSettingModel).where(
                    OrgSettingModel.org_id == org_id, OrgSettingModel.domain == domain
                )
            )
        ).scalar_one_or_none()
        if record is None:
            return {}, []
        return dict(record.config or {}), list(record.locked_keys or [])


async def save_org_config(
    db_mgr: Any, org_id: str, domain: str, config: dict[str, Any], locked_keys: list[str]
) -> None:
    if not _has_database(db_mgr):
        _memory_org[(org_id, domain)] = (dict(config), list(locked_keys))
        return
    now = int(time.time())
    async with db_mgr.session(tenant_org_id=org_id) as session:
        record = (
            await session.execute(
                select(OrgSettingModel).where(
                    OrgSettingModel.org_id == org_id, OrgSettingModel.domain == domain
                )
            )
        ).scalar_one_or_none()
        if record is None:
            session.add(
                OrgSettingModel(
                    id=f"org_cfg_{uuid.uuid4().hex[:12]}",
                    org_id=org_id,
                    domain=domain,
                    config=config,
                    locked_keys=locked_keys,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            record.config = config
            record.locked_keys = locked_keys
            record.updated_at = now


async def load_user_config(db_mgr: Any, org_id: str, user_id: str, domain: str) -> dict[str, Any]:
    """The member's stored overrides for ``domain``."""
    if not _has_database(db_mgr):
        return dict(_memory_user.get((org_id, user_id, domain), {}))
    async with db_mgr.session(tenant_org_id=org_id) as session:
        record = (
            await session.execute(
                select(UserSettingModel).where(
                    UserSettingModel.user_id == user_id,
                    UserSettingModel.org_id == org_id,
                    UserSettingModel.domain == domain,
                )
            )
        ).scalar_one_or_none()
        return dict(record.overrides or {}) if record is not None else {}


async def save_user_config(
    db_mgr: Any, org_id: str, user_id: str, domain: str, overrides: dict[str, Any]
) -> None:
    if not _has_database(db_mgr):
        _memory_user[(org_id, user_id, domain)] = dict(overrides)
        return
    now = int(time.time())
    async with db_mgr.session(tenant_org_id=org_id) as session:
        record = (
            await session.execute(
                select(UserSettingModel).where(
                    UserSettingModel.user_id == user_id,
                    UserSettingModel.org_id == org_id,
                    UserSettingModel.domain == domain,
                )
            )
        ).scalar_one_or_none()
        if record is None:
            session.add(
                UserSettingModel(
                    id=f"user_cfg_{uuid.uuid4().hex[:12]}",
                    user_id=user_id,
                    org_id=org_id,
                    domain=domain,
                    overrides=overrides,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            record.overrides = overrides
            record.updated_at = now


async def delete_user_config(db_mgr: Any, org_id: str, user_id: str, domain: str) -> None:
    if not _has_database(db_mgr):
        _memory_user.pop((org_id, user_id, domain), None)
        return
    async with db_mgr.session(tenant_org_id=org_id) as session:
        record = (
            await session.execute(
                select(UserSettingModel).where(
                    UserSettingModel.user_id == user_id,
                    UserSettingModel.org_id == org_id,
                    UserSettingModel.domain == domain,
                )
            )
        ).scalar_one_or_none()
        if record is not None:
            await session.delete(record)


async def resolve_settings(
    db_mgr: Any, org_id: str, domain: str, user_id: str | None = None
) -> ResolvedSettings:
    """System defaults -> organization -> member override, honouring locked keys."""
    org_config, locked = await load_org_config(db_mgr, org_id, domain)
    overrides = await load_user_config(db_mgr, org_id, user_id, domain) if user_id else {}
    # Keys retired from the registry may linger in old rows; they must not resurface.
    known = specs_for(domain)
    org_config = {k: v for k, v in org_config.items() if k in known}
    overrides = {k: v for k, v in overrides.items() if k in known}
    locked = [k for k in locked if k in known]
    return _resolver.resolve(
        domain=domain, org_config=org_config, locked_keys=locked, user_overrides=overrides
    )


async def effective_settings(
    db_mgr: Any, org_id: str, domain: str, user_id: str | None = None
) -> dict[str, Any]:
    """Just the effective values of :func:`resolve_settings`."""
    return (await resolve_settings(db_mgr, org_id, domain, user_id)).effective
