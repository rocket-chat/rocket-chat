"""Bring-your-own provider keys for members and organizations."""

import logging
import os
from typing import Any

from agent_core.tools.safe_http import resolve_public_target
from config_engine.crypto import CredentialCipher
from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from llm_gateway import PROVIDER_KEY_ENV_VARS
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity, _require_admin
from api.services.audit import (
    AuditService,
)
from api.services.model_credentials import ModelCredentialStore
from api.services.settings import (
    effective_settings,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])


# --- Provider keys (bring your own key) ------------------------------------------------------


class ModelKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=500, description="The provider API key")
    api_base: str | None = Field(
        default=None,
        max_length=500,
        description="Endpoint for self-hosted or proxied providers (Ollama, vLLM, ...)",
    )


class PlatformKeyView(BaseModel):
    provider: str = Field(..., description="The provider the deployment's key is for")
    env_var: str = Field(..., description="The environment variable that carries the key")
    fingerprint: str = Field(..., description="A preview of the key, never the key itself")


def _model_key_store(request: Request) -> ModelCredentialStore:
    store: ModelCredentialStore | None = getattr(request.app.state, "model_credentials", None)
    return (
        store
        if store is not None
        else ModelCredentialStore(getattr(request.app.state, "db_manager", None))
    )


def _stored_key_view(key: Any) -> dict[str, Any]:
    return {
        "provider": key.provider,
        "fingerprint": key.fingerprint,
        "api_base": key.api_base,
        "owner": key.owner,
        "created_at": key.created_at,
    }


async def _check_byok_allowed(request: Request, org_id: str, scope: str) -> None:
    policy = str(
        (
            await effective_settings(
                getattr(request.app.state, "db_manager", None), org_id, "budgets"
            )
        )["byok_policy"]
    )
    if policy == "platform_only" or (policy == "org_key" and scope == "user"):
        raise HTTPException(
            status_code=403,
            detail=f"Your organization's policy (byok_policy={policy}) does not allow this key.",
        )


async def _validate_api_base(api_base: str | None, scope: str) -> None:
    """Members may only point a key at public endpoints; administrators are trusted to name
    internal ones (a private vLLM or Ollama), which is the main reason to set a base URL."""
    if not api_base or scope == "org":
        return
    try:
        await resolve_public_target(api_base)
    except Exception as err:
        raise HTTPException(status_code=422, detail=f"api_base rejected: {err}") from err


@router.get("/user/model-keys")
async def list_my_model_keys(request: Request) -> list[dict[str, Any]]:
    """Provider keys the signed-in member stored (fingerprints only, never the secret)."""
    org_id, user_id, _ = _get_request_identity(request)
    return [_stored_key_view(k) for k in await _model_key_store(request).list(org_id, user_id)]


@router.put("/user/model-keys/{provider}")
async def put_my_model_key(
    provider: str, body: ModelKeyRequest, request: Request
) -> dict[str, Any]:
    """Store or replace the member's own key for a provider."""
    org_id, user_id, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(status_code=403, detail="Forbidden: read-only role")
    await _check_byok_allowed(request, org_id, "user")
    await _validate_api_base(body.api_base, "user")
    key = await _model_key_store(request).put(
        org_id, user_id, provider, body.api_key, body.api_base
    )
    return _stored_key_view(key)


@router.delete("/user/model-keys/{provider}")
async def delete_my_model_key(provider: str, request: Request) -> dict[str, bool]:
    org_id, user_id, _ = _get_request_identity(request)
    return {"deleted": await _model_key_store(request).delete(org_id, user_id, provider)}


@router.get("/admin/model-keys")
async def list_org_model_keys(request: Request) -> list[dict[str, Any]]:
    """Provider keys shared by the organization (administrators)."""
    org_id, _, _ = _require_admin(request)
    return [_stored_key_view(k) for k in await _model_key_store(request).list(org_id, None)]


@router.get("/admin/model-keys/platform", response_model=list[PlatformKeyView])
async def list_platform_model_keys(request: Request) -> list[PlatformKeyView]:
    """Providers whose key the deployment itself holds in the environment (administrators).

    Members use these when neither they nor the organization stored a key; an organization key
    for the same provider replaces the deployment's for this organization only.
    """
    _require_admin(request)
    return [
        PlatformKeyView(
            provider=provider,
            env_var=env_var,
            fingerprint=CredentialCipher.fingerprint(os.environ[env_var]),
        )
        for provider, env_var in PROVIDER_KEY_ENV_VARS.items()
        if os.environ.get(env_var, "").strip()
    ]


@router.put("/admin/model-keys/{provider}")
async def put_org_model_key(
    provider: str, body: ModelKeyRequest, request: Request
) -> dict[str, Any]:
    org_id, admin_user, _ = _require_admin(request)
    await _check_byok_allowed(request, org_id, "org")
    await _validate_api_base(body.api_base, "org")
    key = await _model_key_store(request).put(org_id, None, provider, body.api_key, body.api_base)
    await AuditService(getattr(request.app.state, "db_manager", None)).record(
        "credential_change",
        org_id=org_id,
        actor_user_id=admin_user,
        result_summary=f"set the organization's {provider} key",
        metadata={"provider": provider, "fingerprint": key.fingerprint},
    )
    return _stored_key_view(key)


@router.delete("/admin/model-keys/{provider}")
async def delete_org_model_key(provider: str, request: Request) -> dict[str, bool]:
    org_id, admin_user, _ = _require_admin(request)
    removed = await _model_key_store(request).delete(org_id, None, provider)
    await AuditService(getattr(request.app.state, "db_manager", None)).record(
        "credential_change",
        org_id=org_id,
        actor_user_id=admin_user,
        result_summary=f"removed the organization's {provider} key",
        metadata={"provider": provider},
    )
    return {"deleted": removed}
