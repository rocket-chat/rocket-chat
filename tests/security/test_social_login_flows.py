"""Google and GitHub sign-in and account linking through the real callback route."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from api.auth.session_manager import SESSION_COOKIE_NAME, create_session_token, verify_session_token
from api.routers import auth as auth_module
from fastapi import FastAPI
from fastapi.testclient import TestClient

ORIGIN = "https://rocket.example"

Handler = Callable[[httpx.Request], httpx.Response]


def _providers(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "oauth2.googleapis.com":
        return httpx.Response(200, json={"access_token": "google-token"})
    if host == "www.googleapis.com":
        return httpx.Response(
            200, json={"id": "g-1", "email": "Alice@Acme.Test", "name": "Alice Doe"}
        )
    if host == "github.com" and path == "/login/oauth/access_token":
        return httpx.Response(200, json={"access_token": "gh-token", "token_type": "bearer"})
    if host == "api.github.com":
        return httpx.Response(
            200,
            json={"id": 7, "login": "octo", "email": "Octo@Acme.Test", "name": "Octo Cat"},
            headers={"x-oauth-scopes": "read:user, repo"},
        )
    return httpx.Response(404)


@pytest.fixture
def stored_credentials(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = []

    async def _record(**kwargs: Any) -> None:
        stored.append(kwargs)

    monkeypatch.setattr(auth_module, "store_user_credential", _record)
    return stored


@pytest.fixture
def provider_handler(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Handler]]:
    """Route the callback's outbound calls to a stub; a test may swap the handler."""
    handlers: list[Handler] = [_providers]
    real_client = httpx.AsyncClient

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(lambda r: handlers[0](r)), **kwargs)

    monkeypatch.setattr(auth_module.httpx, "AsyncClient", _client)
    monkeypatch.setenv("NEXTAUTH_URL", ORIGIN)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-secret")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "github-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "github-secret")
    yield handlers


@pytest.fixture
def client(provider_handler: list[Handler]) -> TestClient:
    app = FastAPI()
    app.include_router(auth_module.router)
    return TestClient(app, base_url=ORIGIN, follow_redirects=False)


def _begin(client: TestClient, provider: str, query: str = "") -> str:
    res = client.get(f"/api/auth/{provider}/login{query}")
    assert res.status_code == 302
    return parse_qs(urlparse(res.headers["location"]).query)["state"][0]


def test_google_sign_in_creates_a_session_for_the_normalised_address(client: TestClient) -> None:
    state = _begin(client, "google", "?redirect_uri=/projects")

    done = client.get("/api/auth/google/callback", params={"code": "c", "state": state})

    assert done.status_code == 302 and done.headers["location"] == f"{ORIGIN}/projects"
    session = verify_session_token(client.cookies[SESSION_COOKIE_NAME])
    assert session is not None
    assert session.email == "alice@acme.test" and session.name == "Alice Doe"
    assert session.user_id == "usr_alice_acme_test"


def test_google_token_exchange_failure_returns_to_login_with_the_reason(
    client: TestClient, provider_handler: list[Handler]
) -> None:
    provider_handler[0] = lambda request: httpx.Response(400, text="bad_code")
    state = _begin(client, "google")

    done = client.get("/api/auth/google/callback", params={"code": "c", "state": state})

    assert done.headers["location"].startswith(f"{ORIGIN}/login?error=")
    assert "token%20exchange%20failed" in done.headers["location"]
    assert SESSION_COOKIE_NAME not in client.cookies


def test_github_sign_in_provisions_the_member_and_keeps_the_linked_token(
    client: TestClient, stored_credentials: list[dict[str, Any]]
) -> None:
    state = _begin(client, "github")

    done = client.get("/api/auth/github/callback", params={"code": "c", "state": state})

    assert done.headers["location"] == f"{ORIGIN}/"
    session = verify_session_token(client.cookies[SESSION_COOKIE_NAME])
    assert session is not None and session.email == "octo@acme.test"
    (credential,) = stored_credentials
    assert credential["secret"] == "gh-token" and credential["user_id"] == session.user_id
    assert credential["metadata"]["login"] == "octo"
    assert credential["metadata"]["scopes"] == ["read:user", "repo"]


def test_github_without_a_public_email_gets_a_stable_internal_identity(
    client: TestClient, provider_handler: list[Handler]
) -> None:
    def _no_email(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"id": 7, "login": "octo", "email": None})
        return _providers(request)

    provider_handler[0] = _no_email
    state = _begin(client, "github")

    client.get("/api/auth/github/callback", params={"code": "c", "state": state})

    session = verify_session_token(client.cookies[SESSION_COOKIE_NAME])
    assert session is not None
    assert session.user_id == "usr_gh_7" and session.email == "usr_gh_7@rocket.internal"


def _signed_in(client: TestClient) -> None:
    token = create_session_token(
        user_id="usr_me", org_id="default_org", email="me@acme.test", name="Me", roles=["member"]
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)


def test_linking_attaches_the_token_to_the_signed_in_member_without_a_new_session(
    client: TestClient, stored_credentials: list[dict[str, Any]]
) -> None:
    _signed_in(client)
    before = client.cookies[SESSION_COOKIE_NAME]
    state = _begin(client, "github", "?link_only=true&redirect_uri=/settings/user/git")

    done = client.get("/api/auth/github/callback", params={"code": "c", "state": state})

    assert done.headers["location"] == f"{ORIGIN}/settings/user/git?status=linked"
    assert client.cookies[SESSION_COOKIE_NAME] == before
    (credential,) = stored_credentials
    assert credential["user_id"] == "usr_me" and credential["secret"] == "gh-token"


def test_linking_failure_returns_to_the_settings_page(
    client: TestClient, provider_handler: list[Handler]
) -> None:
    provider_handler[0] = lambda request: httpx.Response(500)
    _signed_in(client)
    state = _begin(client, "github", "?link_only=true&redirect_uri=/settings/user/git")

    done = client.get("/api/auth/github/callback", params={"code": "c", "state": state})

    assert done.headers["location"].startswith(f"{ORIGIN}/settings/user/git?error=")


def test_an_unknown_provider_is_rejected_before_any_call(client: TestClient) -> None:
    done = client.get("/api/auth/gitlab/callback", params={"code": "c", "state": "s"})

    assert "error=InvalidOAuthState" in done.headers["location"]
