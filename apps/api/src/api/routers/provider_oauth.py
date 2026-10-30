"""Signing in to a model provider (OpenRouter) to add a credential, instead of pasting a key."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from api.auth.access import _get_request_identity, _require_admin
from api.auth.capabilities import Capability, require
from api.auth.identity import is_production
from api.auth.origin import public_origin
from api.routers.credentials import _audit, _member_keys_mode, credential_store
from api.services.model_credentials import NewCredential
from api.services.provider_oauth import (
    PROVIDER_OAUTH_COOKIE,
    PROVIDERS,
    ProviderOAuthError,
    exchange_code,
    start_sign_in,
    take_sign_in,
)

router = APIRouter(prefix="/v1", tags=["Credentials"])


class SignInProvider(BaseModel):
    provider: str
    label: str


class SignInUrl(BaseModel):
    authorization_url: str


def _callback_base(request: Request, provider: str) -> str:
    return f"{public_origin(request)}/v1/credentials/oauth/{provider}/callback"


def _settings_page(owner: str) -> str:
    return "/settings/org/models" if owner == "org" else "/settings/user/keys"


@router.get("/credentials/oauth/providers", response_model=list[SignInProvider])
async def sign_in_providers() -> list[SignInProvider]:
    """The providers that offer signing in instead of pasting a key."""
    return [SignInProvider(provider=p.name, label=p.label) for p in PROVIDERS.values()]


@router.post("/credentials/oauth/{provider}/start", response_model=SignInUrl)
async def start_provider_sign_in(
    provider: str,
    request: Request,
    response: Response,
    owner: Literal["member", "org"] = Query("member"),
) -> SignInUrl:
    """Begin signing in: the provider's approval page, and an HttpOnly cookie for the return.
    An organization credential needs an administrator; one's own needs ``bring_own_keys``."""
    if owner == "org":
        org_id, user_id, _ = _require_admin(request)
    else:
        org_id, user_id, _ = await require(request, Capability.BRING_OWN_KEYS)
        if await _member_keys_mode(request, org_id) == "off":
            raise HTTPException(
                status_code=403, detail="Your organization does not use members' own keys."
            )
    try:
        started = start_sign_in(
            provider,
            org_id=org_id,
            user_id=user_id,
            owner=owner,
            callback_base=_callback_base(request, provider),
        )
    except ProviderOAuthError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    response.set_cookie(
        key=PROVIDER_OAUTH_COOKIE,
        value=started.cookie_value,
        max_age=600,
        httponly=True,
        secure=is_production(),
        samesite="lax",
        path="/v1/credentials/oauth",
    )
    return SignInUrl(authorization_url=started.url)


@router.get("/credentials/oauth/{provider}/callback")
async def finish_provider_sign_in(
    provider: str, request: Request, state: str = Query(...), code: str = Query("")
) -> RedirectResponse:
    """The provider sends the browser back here: the code becomes a stored credential of the
    person who started the sign-in (checked against who is signed in now)."""
    org_id, user_id, _ = _get_request_identity(request)
    owner: Literal["member", "org"] = "member"
    try:
        flow = take_sign_in(state, request.cookies.get(PROVIDER_OAUTH_COOKIE))
        owner = "org" if flow.get("owner") == "org" else "member"
        if (flow.get("org_id"), flow.get("user_id"), flow.get("provider")) != (
            org_id,
            user_id,
            provider,
        ):
            raise ProviderOAuthError("This sign-in belongs to someone else.")
        if not code:
            raise ProviderOAuthError("The sign-in was cancelled.")
        key = await exchange_code(provider, code, str(flow["code_verifier"]))
    except ProviderOAuthError as err:
        failed = urlencode({"signin": "failed", "reason": str(err)[:120]})
        return RedirectResponse(url=f"{_settings_page(owner)}?{failed}")
    created = await credential_store(request).create(
        org_id,
        NewCredential(
            owner_kind=owner,
            owner_user_id=None if owner == "org" else user_id,
            provider=provider,
            api_key=key,
            label=f"{PROVIDERS[provider].label} (signed in)",
            kind="oauth",
        ),
    )
    await _audit(request, org_id, user_id, f"signed in to {provider} for a credential", created)
    done = RedirectResponse(url=f"{_settings_page(owner)}?signin=connected&provider={provider}")
    done.delete_cookie(PROVIDER_OAUTH_COOKIE, path="/v1/credentials/oauth")
    return done
