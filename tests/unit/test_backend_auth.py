from unittest.mock import AsyncMock, MagicMock

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
    assert "developer" in me_data["roles"]

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

    roles = await _ensure_db_user_and_org(
        None, "usr_1", "default_org", "dev@example.com", "Dev User"
    )
    assert roles == ["developer"]

    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "admin@example.com")
    admin_roles = await _ensure_db_user_and_org(
        None, "usr_admin", "default_org", "admin@example.com", "Admin User"
    )
    assert "admin" in admin_roles
    assert "owner" in admin_roles


@pytest.mark.asyncio
async def test_ensure_db_user_and_org_first_user_promoted(monkeypatch):
    monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)

    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    # First user: total_users == 0, user does not exist yet
    mock_session.get.return_value = None
    mock_count_res = MagicMock()
    mock_count_res.scalar_one_or_none.return_value = 0
    mock_session.execute.return_value = mock_count_res

    class MockContextManager:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_db.session.return_value = MockContextManager()
    mock_db.session_factory = True

    roles = await _ensure_db_user_and_org(
        mock_db, "usr_first", "default_org", "first@example.com", "First User"
    )
    assert "admin" in roles
    assert "owner" in roles
    assert "developer" in roles

    # Verify user was added to session with role="admin"
    added_user = next(
        call.args[0]
        for call in mock_session.add.call_args_list
        if isinstance(call.args[0], UserModel)
    )
    assert added_user.role == "admin"


@pytest.mark.asyncio
async def test_ensure_db_user_and_org_subsequent_user_not_promoted(monkeypatch):
    monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)

    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    # Subsequent user: total_users == 1
    mock_session.get.return_value = None
    mock_count_res = MagicMock()
    mock_count_res.scalar_one_or_none.return_value = 1
    mock_session.execute.return_value = mock_count_res

    class MockContextManager:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_db.session.return_value = MockContextManager()
    mock_db.session_factory = True

    roles = await _ensure_db_user_and_org(
        mock_db, "usr_second", "default_org", "second@example.com", "Second User"
    )
    assert roles == ["developer"]

    added_user = next(
        call.args[0]
        for call in mock_session.add.call_args_list
        if isinstance(call.args[0], UserModel)
    )
    assert added_user.role == "developer"


@pytest.mark.asyncio
async def test_ensure_db_user_and_org_existing_user_admin_promotion(monkeypatch):
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "promoted@example.com")

    mock_db = MagicMock()
    mock_session = AsyncMock()

    existing_org = OrganizationModel(id="default_org", name="Default Org", slug="default-org")
    existing_user = UserModel(
        id="usr_promoted",
        org_id="default_org",
        email="promoted@example.com",
        name="Promoted User",
        role="developer",
    )

    async def mock_get(model, pk):
        if model == OrganizationModel:
            return existing_org
        if model == UserModel:
            return existing_user
        return None

    mock_session.get.side_effect = mock_get

    class MockContextManager:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_db.session.return_value = MockContextManager()
    mock_db.session_factory = True

    roles = await _ensure_db_user_and_org(
        mock_db, "usr_promoted", "default_org", "promoted@example.com", "Promoted User"
    )
    assert "admin" in roles
    assert "owner" in roles
    # Verify user.role in DB was updated to admin
    assert existing_user.role == "admin"
