"""Sign-in, session tokens and the first members of an organization."""

from __future__ import annotations

import pytest
from api.auth.session_manager import (
    SESSION_COOKIE_NAME,
    create_session_token,
    verify_session_token,
)
from api.db.models import OrganizationModel, UserModel
from api.routers.auth import _ensure_db_user_and_org, is_admin_email
from api.routers.auth import router as auth_router
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(auth_router)
    return app


def test_session_token_roundtrip():
    token = create_session_token(
        user_id="user_123",
        org_id="org_alpha",
        email="user@org.internal",
        name="Alice Tester",
        roles=["developer", "admin"],
        groups=["avionics"],
    )
    assert isinstance(token, str)

    ctx = verify_session_token(token)
    assert ctx is not None
    assert ctx.user_id == "user_123"
    assert ctx.org_id == "org_alpha"
    assert ctx.email == "user@org.internal"
    assert ctx.name == "Alice Tester"
    assert "admin" in ctx.roles
    assert "avionics" in ctx.groups


def test_session_token_expired():
    token = create_session_token(
        user_id="user_expired",
        expires_in=-10,
    )
    ctx = verify_session_token(token)
    assert ctx is None


def test_get_configured_providers(test_app):
    client = TestClient(test_app)
    res = client.get("/api/auth/providers")
    assert res.status_code == 200
    data = res.json()
    assert "google" in data
    assert "github" in data
    assert "oidc" in data
    assert "devLogin" in data


def test_dev_login_and_me_endpoint(test_app):
    client = TestClient(test_app)

    # 1. /api/auth/me should fail without session
    res_unauth = client.get("/api/auth/me")
    assert res_unauth.status_code == 401

    # 2. Login via /api/auth/dev-login
    res_login = client.post(
        "/api/auth/dev-login",
        json={"username": "test_engineer", "org_id": "test_org", "roles": ["developer"]},
    )
    assert res_login.status_code == 200
    login_data = res_login.json()
    assert login_data["status"] == "authenticated"
    assert SESSION_COOKIE_NAME in client.cookies

    # 3. /api/auth/me should now return profile
    res_me = client.get("/api/auth/me")
    assert res_me.status_code == 200
    me_data = res_me.json()
    assert me_data["user_id"] == "usr_test_engineer"
    assert me_data["org_id"] == "test_org"
    assert "member" in me_data["roles"]

    # 4. Logout clears cookie
    res_logout = client.post("/api/auth/logout")
    assert res_logout.status_code == 200

    # Next call to /me should fail
    res_after = client.get("/api/auth/me")
    assert res_after.status_code == 401


def test_oauth_login_redirect_google(test_app, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "mock_google_client_id")
    client = TestClient(test_app)
    res = client.get("/api/auth/google/login", follow_redirects=False)
    assert res.status_code == 302
    location = res.headers["location"]
    assert "accounts.google.com/o/oauth2/v2/auth" in location
    assert "mock_google_client_id" in location
    assert "rocket_oauth_state" in client.cookies


def test_oauth_login_redirect_github(test_app, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "mock_github_client_id")
    client = TestClient(test_app)
    res = client.get("/api/auth/github/login", follow_redirects=False)
    assert res.status_code == 302
    location = res.headers["location"]
    assert "github.com/login/oauth/authorize" in location
    assert "mock_github_client_id" in location


def test_is_admin_email(monkeypatch):
    monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    assert not is_admin_email("user@example.com")
    assert not is_admin_email("")

    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "Admin@Example.com ")
    assert is_admin_email("admin@example.com")
    assert is_admin_email("ADMIN@EXAMPLE.COM")
    assert not is_admin_email("other@example.com")

    monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    monkeypatch.setenv("ADMIN_EMAILS", "alice@example.com, bob@example.com ")
    assert is_admin_email("alice@example.com")
    assert is_admin_email("bob@example.com")
    assert not is_admin_email("charlie@example.com")


@pytest.mark.asyncio
async def test_ensure_db_user_and_org_fallback_without_db(monkeypatch):
    monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)

    _, roles = await _ensure_db_user_and_org(
        None, "usr_1", "default_org", "dev@example.com", "Dev User"
    )
    assert roles == ["member"]

    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "admin@example.com")
    _, admin_roles = await _ensure_db_user_and_org(
        None, "usr_admin", "default_org", "admin@example.com", "Admin User"
    )
    assert "admin" in admin_roles


class _Membership:
    def __init__(self, role: str) -> None:
        self.role = role


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, model: object, key: str) -> object:
        return OrganizationModel(id=key, name=key, slug=key)

    def add(self, row: object) -> None:
        return None

    async def flush(self) -> None:
        return None


class _Db:
    session_factory = True

    def session(self, **_: object) -> _Session:
        return _Session()


class _Org:
    """Sign-in against stubbed membership storage: who is already a member, and what is saved."""

    def __init__(self, members: dict[str, str]) -> None:
        self.members = dict(members)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import api.routers.auth as auth

        self.db = _Db()

        async def person(
            session: object, user_id: str, org_id: str, email: str, name: str, idp: object
        ) -> UserModel:
            return UserModel(id=user_id, org_id=None, email=email, name=name, role="")

        async def membership(session: object, user_id: str, org_id: str) -> _Membership | None:
            return _Membership(self.members[user_id]) if user_id in self.members else None

        async def count(session: object, org_id: str, exclude: tuple[str, ...] = ()) -> int:
            return len(self.members)

        async def ensure(session: object, user_id: str, org_id: str, role: str) -> _Membership:
            self.members[user_id] = role
            return _Membership(role)

        monkeypatch.setattr(auth, "_person", person)
        monkeypatch.setattr(auth, "membership_row", membership)
        monkeypatch.setattr(auth, "member_count", count)
        monkeypatch.setattr(auth, "ensure_membership", ensure)


@pytest.mark.asyncio
async def test_the_first_member_of_an_organization_is_its_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    org = _Org({})
    org.install(monkeypatch)

    _, first = await _ensure_db_user_and_org(
        org.db, "usr_first", "default_org", "f@example.com", "F"
    )  # type: ignore[arg-type]
    _, second = await _ensure_db_user_and_org(
        org.db, "usr_second", "default_org", "s@example.com", "S"
    )  # type: ignore[arg-type]

    assert "admin" in first and org.members["usr_first"] == "admin"
    assert second == ["member"] and org.members["usr_second"] == "member"


@pytest.mark.asyncio
async def test_an_admin_email_promotes_an_existing_member(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "promoted@example.com")
    org = _Org({"usr_promoted": "member", "someone": "admin"})
    org.install(monkeypatch)

    _, roles = await _ensure_db_user_and_org(
        org.db,  # type: ignore[arg-type]
        "usr_promoted",
        "default_org",
        "promoted@example.com",
        "Promoted User",
    )

    assert "admin" in roles
