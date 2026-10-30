"""Model provider credentials of the organization and of each member.

Every change (create, rotate, update, delete) is audited for organization *and* member
credentials, with the fingerprint, never the secret. A member credential can be shared with
organization agents only by its owner; administrators then bind it to those agents.
"""

import logging
import os
from typing import Literal

from agent_core.tools.safe_http import resolve_public_target
from config_engine.crypto import CredentialCipher
from fastapi import APIRouter, HTTPException, Request
from llm_gateway import PROVIDER_KEY_ENV_VARS
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity, _require_admin
from api.auth.capabilities import Capability, require
from api.auth.roles import require_writer
from api.services.audit import AuditService
from api.services.model_credentials import (
    CredentialChanges,
    ModelCredentialStore,
    NewCredential,
    StoredCredential,
)
from api.services.settings import effective_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Model credentials"])


class CredentialView(BaseModel):
    id: str
    label: str
    provider: str
    kind: str
    owner: Literal["org", "user"]
    owner_user_id: str | None = None
    fingerprint: str = Field(..., description="A preview of the key, never the key itself")
    api_base: str | None = None
    default_for_provider: bool
    restricted_to_agents: bool
    shared_with_org_agents: list[str]
    created_at: int
    updated_at: int


class CreateCredentialRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=50)
    api_key: str = Field(..., min_length=8, max_length=500, description="The provider API key")
    label: str = Field(default="", max_length=255)
    api_base: str | None = Field(
        default=None,
        max_length=500,
        description="Endpoint for self-hosted or proxied providers (Ollama, vLLM, ...)",
    )
    default_for_provider: bool = True
    restricted_to_agents: bool = Field(
        default=False,
        description="Skip it in the default chain: only the agents bound to it use it (admins)",
    )


class UpdateCredentialRequest(BaseModel):
    label: str | None = Field(default=None, max_length=255)
    api_base: str | None = Field(default=None, max_length=500)
    clear_api_base: bool = False
    default_for_provider: bool | None = None
    restricted_to_agents: bool | None = None
    shared_with_org_agents: list[str] | None = Field(
        default=None, description="Organization agents that may use this member credential"
    )


class RotateCredentialRequest(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=500)


class OrgCredentialList(BaseModel):
    credentials: list[CredentialView]
    # Member credentials their owners shared with organization agents (bindable by admins).
    shared_by_members: list[CredentialView]


class PlatformKeyView(BaseModel):
    provider: str = Field(..., description="The provider the deployment's key is for")
    env_var: str = Field(..., description="The environment variable that carries the key")
    fingerprint: str = Field(..., description="A preview of the key, never the key itself")


def credential_store(request: Request) -> ModelCredentialStore:
    store: ModelCredentialStore | None = getattr(request.app.state, "model_credentials", None)
    return (
        store
        if store is not None
        else ModelCredentialStore(getattr(request.app.state, "db_manager", None))
    )


def _view(item: StoredCredential) -> CredentialView:
    return CredentialView(
        id=item.id,
        label=item.label,
        provider=item.provider,
        kind=item.kind,
        owner=item.owner,
        owner_user_id=item.owner_user_id,
        fingerprint=item.fingerprint,
        api_base=item.api_base,
        default_for_provider=item.default_for_provider,
        restricted_to_agents=item.restricted_to_agents,
        shared_with_org_agents=list(item.shared_with_org_agents),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


async def _audit(
    request: Request, org_id: str, actor: str, summary: str, item: StoredCredential
) -> None:
    await AuditService(getattr(request.app.state, "db_manager", None)).record(
        "credential_change",
        org_id=org_id,
        actor_user_id=actor,
        result_summary=summary,
        metadata={
            "credential_id": item.id,
            "provider": item.provider,
            "owner": item.owner,
            "fingerprint": item.fingerprint,
        },
    )


async def _validate_api_base(api_base: str | None, owner: str) -> None:
    """Members may only point a key at public endpoints; administrators are trusted to name
    internal ones (a private vLLM or Ollama), which is the main reason to set a base URL."""
    if not api_base or owner == "org":
        return
    try:
        await resolve_public_target(api_base)
    except Exception as err:
        raise HTTPException(status_code=422, detail=f"api_base rejected: {err}") from err


async def _member_keys_mode(request: Request, org_id: str) -> str:
    models = await effective_settings(
        getattr(request.app.state, "db_manager", None), org_id, "models"
    )
    return str(models.get("member_keys") or "preferred")


async def _own(request: Request, org_id: str, user_id: str, credential_id: str) -> StoredCredential:
    found = await credential_store(request).get(org_id, credential_id)
    if found is None or found.owner_kind != "member" or found.owner_user_id != user_id:
        raise HTTPException(status_code=404, detail="Credential not found")
    return found


async def _org(request: Request, org_id: str, credential_id: str) -> StoredCredential:
    found = await credential_store(request).get(org_id, credential_id)
    if found is None or found.owner_kind != "org":
        raise HTTPException(status_code=404, detail="Credential not found")
    return found


# -- a member's own credentials -----------------------------------------------------------


@router.get("/user/credentials", response_model=list[CredentialView])
async def list_my_credentials(request: Request) -> list[CredentialView]:
    """The signed-in member's own provider credentials (fingerprints only)."""
    org_id, user_id, _ = _get_request_identity(request)
    return [
        _view(c)
        for c in await credential_store(request).list(
            org_id, owner_user_id=user_id, owner_kind="member"
        )
    ]


@router.post("/user/credentials", response_model=CredentialView)
async def create_my_credential(body: CreateCredentialRequest, request: Request) -> CredentialView:
    """Store a provider key of the member's own (needs the ``bring_own_keys`` capability)."""
    org_id, user_id, _ = await require(request, Capability.BRING_OWN_KEYS)
    if await _member_keys_mode(request, org_id) == "off":
        raise HTTPException(
            status_code=403, detail="Your organization does not use members' own keys."
        )
    await _validate_api_base(body.api_base, "member")
    created = await credential_store(request).create(
        org_id,
        NewCredential(
            owner_kind="member",
            owner_user_id=user_id,
            provider=body.provider,
            api_key=body.api_key,
            label=body.label,
            api_base=body.api_base,
            default_for_provider=body.default_for_provider,
        ),
    )
    await _audit(request, org_id, user_id, f"added a {body.provider} key of their own", created)
    return _view(created)


@router.patch("/user/credentials/{credential_id}", response_model=CredentialView)
async def update_my_credential(
    credential_id: str, body: UpdateCredentialRequest, request: Request
) -> CredentialView:
    """Rename, re-point, make default, or share with organization agents (the owner only)."""
    org_id, user_id, roles = _get_request_identity(request)
    require_writer(roles, "change credentials")
    current = await _own(request, org_id, user_id, credential_id)
    await _validate_api_base(body.api_base, "member")
    updated = await credential_store(request).update(
        org_id,
        credential_id,
        CredentialChanges(
            label=body.label,
            api_base=body.api_base,
            clear_api_base=body.clear_api_base,
            default_for_provider=body.default_for_provider,
            shared_with_org_agents=tuple(dict.fromkeys(body.shared_with_org_agents))
            if body.shared_with_org_agents is not None
            else None,
        ),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    summary = "updated their own credential"
    if set(updated.shared_with_org_agents) != set(current.shared_with_org_agents):
        summary = "changed which organization agents may use their credential"
    await _audit(request, org_id, user_id, summary, updated)
    return _view(updated)


@router.post("/user/credentials/{credential_id}/rotate", response_model=CredentialView)
async def rotate_my_credential(
    credential_id: str, body: RotateCredentialRequest, request: Request
) -> CredentialView:
    org_id, user_id, roles = _get_request_identity(request)
    require_writer(roles, "change credentials")
    await _own(request, org_id, user_id, credential_id)
    rotated = await credential_store(request).rotate(org_id, credential_id, body.api_key)
    if rotated is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    await _audit(request, org_id, user_id, "rotated their own credential", rotated)
    return _view(rotated)


@router.delete("/user/credentials/{credential_id}")
async def delete_my_credential(credential_id: str, request: Request) -> dict[str, bool]:
    org_id, user_id, _ = _get_request_identity(request)
    await _own(request, org_id, user_id, credential_id)
    removed = await credential_store(request).delete(org_id, credential_id)
    if removed is not None:
        await _audit(request, org_id, user_id, "removed their own credential", removed)
    return {"deleted": removed is not None}


# -- the organization's credentials -------------------------------------------------------


@router.get("/admin/credentials", response_model=OrgCredentialList)
async def list_org_credentials(request: Request) -> OrgCredentialList:
    """The organization's credentials, and members' credentials shared with its agents."""
    org_id, _, _ = _require_admin(request)
    everything = await credential_store(request).list(org_id)
    return OrgCredentialList(
        credentials=[_view(c) for c in everything if c.owner_kind == "org"],
        shared_by_members=[
            _view(c) for c in everything if c.owner_kind == "member" and c.shared_with_org_agents
        ],
    )


@router.get("/admin/credentials/platform", response_model=list[PlatformKeyView])
async def list_platform_credentials(request: Request) -> list[PlatformKeyView]:
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


@router.post("/admin/credentials", response_model=CredentialView)
async def create_org_credential(body: CreateCredentialRequest, request: Request) -> CredentialView:
    """Add an organization credential; a restricted one serves only the agents bound to it."""
    org_id, admin_id, _ = _require_admin(request)
    await _validate_api_base(body.api_base, "org")
    created = await credential_store(request).create(
        org_id,
        NewCredential(
            owner_kind="org",
            owner_user_id=None,
            provider=body.provider,
            api_key=body.api_key,
            label=body.label,
            api_base=body.api_base,
            default_for_provider=body.default_for_provider,
            restricted_to_agents=body.restricted_to_agents,
        ),
    )
    await _audit(request, org_id, admin_id, f"added an organization {body.provider} key", created)
    return _view(created)


@router.patch("/admin/credentials/{credential_id}", response_model=CredentialView)
async def update_org_credential(
    credential_id: str, body: UpdateCredentialRequest, request: Request
) -> CredentialView:
    org_id, admin_id, _ = _require_admin(request)
    await _org(request, org_id, credential_id)
    if body.shared_with_org_agents is not None:
        raise HTTPException(
            status_code=422, detail="Organization credentials are bound on the agent instead."
        )
    await _validate_api_base(body.api_base, "org")
    updated = await credential_store(request).update(
        org_id,
        credential_id,
        CredentialChanges(
            label=body.label,
            api_base=body.api_base,
            clear_api_base=body.clear_api_base,
            default_for_provider=body.default_for_provider,
            restricted_to_agents=body.restricted_to_agents,
        ),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    await _audit(request, org_id, admin_id, "updated an organization credential", updated)
    return _view(updated)


@router.post("/admin/credentials/{credential_id}/rotate", response_model=CredentialView)
async def rotate_org_credential(
    credential_id: str, body: RotateCredentialRequest, request: Request
) -> CredentialView:
    org_id, admin_id, _ = _require_admin(request)
    await _org(request, org_id, credential_id)
    rotated = await credential_store(request).rotate(org_id, credential_id, body.api_key)
    if rotated is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    await _audit(request, org_id, admin_id, "rotated an organization credential", rotated)
    return _view(rotated)


@router.delete("/admin/credentials/{credential_id}")
async def delete_org_credential(credential_id: str, request: Request) -> dict[str, bool]:
    """Remove an organization credential; agents bound to it fail with "credential withdrawn"."""
    org_id, admin_id, _ = _require_admin(request)
    await _org(request, org_id, credential_id)
    removed = await credential_store(request).delete(org_id, credential_id)
    if removed is not None:
        await _audit(request, org_id, admin_id, "removed an organization credential", removed)
    return {"deleted": removed is not None}
