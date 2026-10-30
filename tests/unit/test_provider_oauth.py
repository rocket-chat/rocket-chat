"""Signing in to OpenRouter adds a credential of the person who started it, and only theirs."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from api.routers import provider_oauth as routes
from api.services import provider_oauth
from fastapi.testclient import TestClient

MEMBER = {"X-Tenant-Org-Id": "oauth_org", "X-Tenant-User-Id": "ada", "X-User-Role": "admin"}


@pytest.fixture(autouse=True)
async def _identity() -> AsyncIterator[None]:
    """The credential rows reference the person; the in-memory ``client`` provisions nobody."""
    if not (os.getenv("DATABASE_URL") or os.getenv("TEST_DATABASE_URL")):
        yield
        return
    from api.db.session import DatabaseSessionManager
    from api.db.store import ensure_tenant_identity

    manager = DatabaseSessionManager(os.environ["ADMIN_DATABASE_URL"])
    try:
        async with manager.session(tenant_org_id="oauth_org") as session:
            await ensure_tenant_identity(session, "oauth_org", "ada")
        yield
    finally:
        await manager.close()


def _start(client: TestClient, owner: str = "member") -> tuple[str, str]:
    started = client.post(f"/v1/credentials/oauth/openrouter/start?owner={owner}", headers=MEMBER)
    assert started.status_code == 200, started.text
    url = urlparse(started.json()["authorization_url"])
    callback = parse_qs(url.query)["callback_url"][0]
    assert parse_qs(url.query)["code_challenge_method"] == ["S256"]
    state = parse_qs(urlparse(callback).query)["state"][0]
    return state, started.cookies[provider_oauth.PROVIDER_OAUTH_COOKIE]


def test_a_sign_in_stores_the_issued_key_as_an_oauth_credential(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def exchange(provider: str, code: str, verifier: str) -> str:
        seen.update(provider=provider, code=code, verifier=verifier)
        return "sk-or-v1-ISSUED-KEY-9876"

    monkeypatch.setattr(routes, "exchange_code", exchange)
    state, cookie = _start(client)
    client.cookies.set(provider_oauth.PROVIDER_OAUTH_COOKIE, cookie)
    done = client.get(
        "/v1/credentials/oauth/openrouter/callback",
        params={"state": state, "code": "c-1"},
        headers=MEMBER,
        follow_redirects=False,
    )
    assert done.status_code == 307 and "signin=connected" in done.headers["location"]
    assert seen["code"] == "c-1" and seen["verifier"]
    mine = client.get("/v1/user/credentials", headers=MEMBER).json()
    stored = [c for c in mine if c["provider"] == "openrouter" and c["kind"] == "oauth"]
    assert stored and stored[-1]["fingerprint"].endswith("9876")
    assert "ISSUED-KEY" not in str(mine)


def test_a_sign_in_cannot_be_finished_by_someone_else_or_another_browser(
    client: TestClient,
) -> None:
    state, cookie = _start(client)
    other = {**MEMBER, "X-Tenant-User-Id": "mallory"}
    client.cookies.set(provider_oauth.PROVIDER_OAUTH_COOKIE, cookie)
    stolen = client.get("/v1/credentials/oauth/openrouter/callback", params={"state": state, "code": "c"},
                        headers=other, follow_redirects=False)  # fmt: skip
    assert "signin=failed" in stolen.headers["location"]
    client.cookies.clear()
    no_cookie = client.get("/v1/credentials/oauth/openrouter/callback", params={"state": state, "code": "c"},
                           headers=MEMBER, follow_redirects=False)  # fmt: skip
    assert "signin=failed" in no_cookie.headers["location"]


def test_only_offered_providers_and_administrators_for_organization_keys(
    client: TestClient,
) -> None:
    assert client.post("/v1/credentials/oauth/anthropic/start", headers=MEMBER).status_code == 404
    member = {**MEMBER, "X-User-Role": "member"}
    assert (
        client.post("/v1/credentials/oauth/openrouter/start?owner=org", headers=member).status_code
        == 403
    )
    assert [
        p["provider"] for p in client.get("/v1/credentials/oauth/providers", headers=MEMBER).json()
    ] == ["openrouter"]
