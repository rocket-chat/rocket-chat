"""Signing in to a model provider instead of pasting a key, where the provider offers it to
third-party applications.

OpenRouter does ("Sign in with OpenRouter", OAuth PKCE): the person approves on OpenRouter and the
platform receives a key of theirs, stored like any other credential (``kind="oauth"``). Consumer
subscriptions (Claude Pro/Max, ChatGPT Plus) are not offered: their providers do not allow their
use from third-party applications.

The flow keeps nothing in a process, so any replica finishes it: the state is a signed token
naming the person, the organization and whose credential it becomes, and the PKCE verifier
travels only in an HttpOnly cookie bound to that state.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from api.auth import oauth_state
from api.services.mcp_engine import generate_pkce_pair

PROVIDER_OAUTH_COOKIE = "rocket_provider_oauth"
EXCHANGE_TIMEOUT_SECONDS = 15.0


class ProviderOAuthError(ValueError):
    """The sign-in could not be completed; the message says why."""


@dataclass(frozen=True)
class OAuthProvider:
    """A provider's sign-in: where the person approves, and where the code becomes a key."""

    name: str
    label: str
    authorize_url: str
    exchange_url: str


PROVIDERS: dict[str, OAuthProvider] = {
    "openrouter": OAuthProvider(
        name="openrouter",
        label="OpenRouter",
        authorize_url="https://openrouter.ai/auth",
        exchange_url="https://openrouter.ai/api/v1/auth/keys",
    ),
}


@dataclass(frozen=True)
class SignInStart:
    url: str
    cookie_value: str


def start_sign_in(
    provider: str, *, org_id: str, user_id: str, owner: str, callback_base: str
) -> SignInStart:
    """The provider URL to send the browser to, and the cookie that must come back with it.
    ``owner`` is ``member`` (the person's own key) or ``org`` (an organization key)."""
    found = PROVIDERS.get(provider)
    if found is None:
        raise ProviderOAuthError(f"Signing in is not offered for '{provider}'.")
    verifier, challenge = generate_pkce_pair()
    state = oauth_state.sign(
        {
            "provider": provider,
            "org_id": org_id,
            "user_id": user_id,
            "owner": owner,
            "nonce": secrets.token_urlsafe(8),
        }
    )
    # OpenRouter returns only ``code`` to the callback: the state rides in the callback URL.
    callback = f"{callback_base}?{urlencode({'state': state})}"
    query = urlencode(
        {"callback_url": callback, "code_challenge": challenge, "code_challenge_method": "S256"}
    )
    return SignInStart(
        url=f"{found.authorize_url}?{query}",
        cookie_value=oauth_state.sign({"state": state, "cv": verifier}),
    )


def take_sign_in(state: str, cookie_value: str | None) -> dict[str, Any]:
    """The flow's claims and verifier, when the state is genuine and this browser started it."""
    try:
        claims = oauth_state.verify(state)
        flow = oauth_state.verify(cookie_value or "")
    except ValueError as err:
        raise ProviderOAuthError("This sign-in expired or was not started here.") from err
    if flow.get("state") != state:
        raise ProviderOAuthError("This sign-in was started in another browser.")
    return {**claims, "code_verifier": flow["cv"]}


async def exchange_code(provider: str, code: str, verifier: str) -> str:
    """The key the provider issues for an approved sign-in."""
    found = PROVIDERS.get(provider)
    if found is None:
        raise ProviderOAuthError(f"Signing in is not offered for '{provider}'.")
    try:
        async with httpx.AsyncClient(timeout=EXCHANGE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                found.exchange_url,
                json={"code": code, "code_verifier": verifier, "code_challenge_method": "S256"},
            )
    except httpx.HTTPError as err:
        raise ProviderOAuthError(f"{found.label} could not be reached.") from err
    key = response.json().get("key") if response.status_code == 200 else None
    if not key:
        raise ProviderOAuthError(f"{found.label} did not issue a key ({response.status_code}).")
    return str(key)
