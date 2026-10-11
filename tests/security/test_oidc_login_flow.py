"""The HTTP login flow around OIDC: signed state, cookie binding, verified email."""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from api.auth import oauth_state, oidc
from api.auth.session_manager import SESSION_COOKIE_NAME, verify_session_token
from api.routers.auth import router as auth_router
from fake_oidc_provider import CLIENT_ID, CLIENT_SECRET, ISSUER, FakeProvider
from fastapi import FastAPI
from fastapi.testclient import TestClient

ORIGIN = "https://rocket.example"


@pytest.fixture
def idp(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeProvider]:
    provider = FakeProvider()
    oidc.reset_caches()
    oidc._transport_override = httpx.ASGITransport(app=provider.app())
    oidc._oauth_transport_override = provider.oauth_transport()
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("NEXTAUTH_URL", ORIGIN)
    monkeypatch.delenv("OIDC_DISCOVERY_URL", raising=False)
    yield provider
    oidc._transport_override = None
    oidc._oauth_transport_override = None
    oidc.reset_caches()


@pytest.fixture
def client(idp: FakeProvider) -> TestClient:
    app = FastAPI()
    app.include_router(auth_router)
    return TestClient(app, base_url=ORIGIN, follow_redirects=False)


def _start(client: TestClient, idp: FakeProvider, **claims: object) -> tuple[str, str]:
    """Run /login and the provider consent; returns the (code, state) the callback receives."""
    res = client.get("/api/auth/oidc/login")
    assert res.status_code == 302
    code, query = idp.approve(res.headers["location"], **claims)
    return code, query["state"][0]


def test_login_redirects_to_the_discovered_provider_with_signed_state(
    client: TestClient, idp: FakeProvider
) -> None:
    res = client.get("/api/auth/oidc/login?redirect_uri=/projects")
    query = parse_qs(urlparse(res.headers["location"]).query)

    assert res.headers["location"].startswith(
        "https://login.idp.test/tenant-42/oauth2/v2.0/authorize"
    )
    state = oauth_state.verify(query["state"][0])
    assert state["provider"] == "oidc" and state["return_to"] == "/projects"
    cookie = oauth_state.verify(client.cookies["rocket_oauth_state"])
    assert cookie["state"] == query["state"][0]
    assert "cv" in cookie and cookie["nonce"] == query["nonce"][0]
    assert cookie["cv"] not in res.headers["location"]


def test_a_successful_callback_creates_a_session_for_the_verified_identity(
    client: TestClient, idp: FakeProvider
) -> None:
    code, state = _start(client, idp, email="alice@acme.test")

    done = client.get("/api/auth/oidc/callback", params={"code": code, "state": state})

    assert done.status_code == 302 and done.headers["location"] == f"{ORIGIN}/"
    session = verify_session_token(client.cookies[SESSION_COOKIE_NAME])
    assert session is not None and session.email == "alice@acme.test"
    assert session.user_id == "idp-user-1"


def test_an_unverified_email_is_refused(client: TestClient, idp: FakeProvider) -> None:
    code, state = _start(client, idp, email_verified=False)

    done = client.get("/api/auth/oidc/callback", params={"code": code, "state": state})

    assert "error=EmailNotVerified" in done.headers["location"]
    assert SESSION_COOKIE_NAME not in client.cookies


def test_a_forged_state_parameter_is_refused(client: TestClient, idp: FakeProvider) -> None:
    client.get("/api/auth/oidc/login")
    forged = oauth_state.sign({"provider": "oidc", "link_only": True, "user_id": "victim"})
    # Re-sign the browser cookie so only the *state* differs from what login issued.
    client.cookies.set("rocket_oauth_state", oauth_state.sign({"state": forged}))

    done = client.get("/api/auth/oidc/callback", params={"code": "c", "state": forged + "x"})

    assert "error=InvalidOAuthState" in done.headers["location"]


def test_the_state_must_come_back_in_the_browser_that_started_the_login(
    client: TestClient, idp: FakeProvider
) -> None:
    code, state = _start(client, idp)
    other_browser = TestClient(client.app, base_url=ORIGIN, follow_redirects=False)  # no cookie

    done = other_browser.get("/api/auth/oidc/callback", params={"code": code, "state": state})

    assert "error=InvalidOAuthState" in done.headers["location"]


def test_a_state_issued_for_another_provider_is_refused(
    client: TestClient, idp: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_ID", "gh")
    github = client.get("/api/auth/github/login")
    state = parse_qs(urlparse(github.headers["location"]).query)["state"][0]

    done = client.get("/api/auth/oidc/callback", params={"code": "c", "state": state})

    assert "error=InvalidOAuthState" in done.headers["location"]
