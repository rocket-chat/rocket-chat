"""Typed settings: schema, member values and organization policy."""

import logging
from typing import Any

from config_engine import REGISTRY, FieldError, validate_locked_keys
from config_engine import validate as validate_setting
from config_engine.registry import writable_keys
from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)

from api.auth.access import _get_request_identity, _require_admin
from api.models import (
    UpdateOrgSettingsRequest,
    UpdateUserSettingsRequest,
)
from api.services.audit import (
    AuditService,
)
from api.services.settings import (
    delete_user_config,
    load_org_config,
    load_user_config,
    resolve_settings,
    save_org_config,
    save_user_config,
)
from api.turns.shared import _settings_resolver

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])


def _settings_validation_error(errors: list[FieldError]) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=[{"key": e.key, "message": e.message} for e in errors],
    )


def _require_known_domain(domain: str) -> None:
    if domain not in REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown settings domain '{domain}'")


@router.get("/settings-schema")
async def get_settings_schema(request: Request) -> dict[str, Any]:
    """Every settings domain's fields (type, scope, default, constraints) for form rendering.

    The cockpit builds its organization and member settings forms from this, so a field
    exists in the UI if and only if it is declared in the registry.
    """
    _get_request_identity(request)
    return {
        "domains": {
            domain: [
                {
                    "key": spec.key,
                    "kind": spec.kind,
                    "default": spec.default,
                    "scope": spec.scope,
                    "description": spec.description,
                    "choices": list(spec.choices),
                    "minimum": spec.minimum,
                    "maximum": spec.maximum,
                    "label": spec.label,
                    "unit": spec.unit,
                    "advanced": spec.advanced,
                    "deprecated": spec.deprecated,
                }
                for spec in specs.values()
            ]
            for domain, specs in REGISTRY.items()
        }
    }


@router.get("/settings/{domain}")
async def get_domain_settings(domain: str, request: Request) -> dict[str, Any]:
    """Retrieve cascading settings for the current user and organization."""
    org_id, user_id, _ = _get_request_identity(request)
    _require_known_domain(domain)
    db_mgr = getattr(request.app.state, "db_manager", None)
    return (await resolve_settings(db_mgr, org_id, domain, user_id)).to_dict()


@router.put("/settings/{domain}")
async def update_user_domain_settings(
    domain: str,
    body: UpdateUserSettingsRequest,
    request: Request,
) -> dict[str, Any]:
    """Save member overrides for a domain; org-managed or locked keys are rejected."""
    org_id, user_id, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(
            status_code=403, detail="Forbidden: Read-only role cannot update user settings"
        )
    _require_known_domain(domain)
    checked = validate_setting(domain, body.overrides, writer="user")
    if not checked.ok:
        raise _settings_validation_error(checked.errors)

    db_mgr = getattr(request.app.state, "db_manager", None)
    _, locked_keys = await load_org_config(db_mgr, org_id, domain)
    allowed, rejected = _settings_resolver.filter_valid_user_overrides(
        domain, checked.values, locked_keys
    )
    current = await load_user_config(db_mgr, org_id, user_id, domain)
    current.update(allowed)
    await save_user_config(db_mgr, org_id, user_id, domain, current)
    await AuditService(db_mgr).record(
        "settings_change",
        org_id=org_id,
        actor_user_id=user_id,
        result_summary=f"updated user settings domain '{domain}'",
        metadata={"domain": domain, "keys": sorted(allowed.keys())},
    )

    res = (await resolve_settings(db_mgr, org_id, domain, user_id)).to_dict()
    if rejected:
        res["warning"] = f"Locked keys ignored by organization policy: {rejected}"
    return res


@router.delete("/settings/{domain}")
async def reset_user_domain_settings(domain: str, request: Request) -> dict[str, Any]:
    """Reset member overrides for a domain back to organization defaults."""
    org_id, user_id, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(
            status_code=403, detail="Forbidden: Read-only role cannot reset settings"
        )
    _require_known_domain(domain)
    db_mgr = getattr(request.app.state, "db_manager", None)
    await delete_user_config(db_mgr, org_id, user_id, domain)
    return (await resolve_settings(db_mgr, org_id, domain, None)).to_dict()


@router.delete("/settings/{domain}/{key}")
async def reset_user_setting(domain: str, key: str, request: Request) -> dict[str, Any]:
    """Remove one personal override: the setting follows the organization's value again,
    including its later changes."""
    org_id, user_id, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(
            status_code=403, detail="Forbidden: Read-only role cannot reset settings"
        )
    _require_known_domain(domain)
    db_mgr = getattr(request.app.state, "db_manager", None)
    current = await load_user_config(db_mgr, org_id, user_id, domain)
    if current.pop(key, None) is not None:
        await save_user_config(db_mgr, org_id, user_id, domain, current)
    return (await resolve_settings(db_mgr, org_id, domain, user_id)).to_dict()


@router.get("/admin/settings/{domain}")
async def get_org_admin_settings(domain: str, request: Request) -> dict[str, Any]:
    """Retrieve organization-wide policy configuration and locked keys (admin only)."""
    org_id, _, _ = _require_admin(request)
    _require_known_domain(domain)
    db_mgr = getattr(request.app.state, "db_manager", None)
    org_conf, locked_keys = await load_org_config(db_mgr, org_id, domain)
    org_keys = writable_keys(domain, "org")
    system_defaults = {
        k: v for k, v in _settings_resolver.get_system_defaults(domain).items() if k in org_keys
    }
    merged_config = dict(system_defaults)
    merged_config.update({k: v for k, v in org_conf.items() if k in org_keys})
    return {
        "domain": domain,
        "config": merged_config,
        "locked_keys": locked_keys,
        "system_defaults": system_defaults,
    }


@router.put("/admin/settings/{domain}")
async def update_org_admin_settings(
    domain: str,
    body: UpdateOrgSettingsRequest,
    request: Request,
) -> dict[str, Any]:
    """Replace the organization's defaults and locked keys for a domain (admin only)."""
    org_id, admin_user, _ = _require_admin(request)
    _require_known_domain(domain)
    checked = validate_setting(domain, body.config, writer="org")
    errors = [*checked.errors, *validate_locked_keys(domain, body.locked_keys)]
    if errors:
        raise _settings_validation_error(errors)

    db_mgr = getattr(request.app.state, "db_manager", None)
    await save_org_config(db_mgr, org_id, domain, checked.values, body.locked_keys)
    # Settings-mutation audit: keep the admin change (keys only, not values)
    # for the "who changed what policy, when" question.
    await AuditService(db_mgr).record(
        "settings_change",
        org_id=org_id,
        actor_user_id=admin_user,
        result_summary=f"updated org settings domain '{domain}'",
        metadata={"domain": domain, "keys": sorted(checked.values.keys())},
    )
    return {
        "status": "ok",
        "domain": domain,
        "config": checked.values,
        "locked_keys": body.locked_keys,
    }
