"""Slack account pairing and workspace binding."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from api.auth.session_manager import SESSION_COOKIE_NAME, create_session_token
from api.db.models import OrganizationModel, UserModel
from api.db.session import DatabaseSessionManager
from api.main import app
from api.services.slack_link import (
    PairingCodes,
    bind_workspace,
    find_member,
    link_member,
    reset_memory_workspaces,
    unbind_workspace,
    workspace_org,
)
from fastapi.testclient import TestClient

ADMIN_URL = os.getenv("ADMIN_DATABASE_URL", "")


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_codes_are_single_use_and_expire() -> None:
    clock = _Clock()
    codes = PairingCodes(ttl_seconds=600, clock=clock)
    code = await codes.begin("U1", "T1")
    assert len(code) == 8
    assert await codes.complete(code.lower()) == ("U1", "T1")  # case-insensitive
    assert await codes.complete(code) is None  # single use

    stale = await codes.begin("U2", "T1")
    clock.now += 601
    assert await codes.complete(stale) is None


@pytest.mark.asyncio
async def test_a_new_code_revokes_the_users_previous_one() -> None:
    codes = PairingCodes()
    first = await codes.begin("U1", "T1")
    second = await codes.begin("U1", "T1")
    assert await codes.complete(first) is None
    assert await codes.complete(second) == ("U1", "T1")


@pytest.mark.asyncio
async def test_workspace_binding_without_a_database() -> None:
    reset_memory_workspaces()
    assert await workspace_org(None, "T1") is None
    await bind_workspace(None, "T1", "org_a", "admin")
    assert await workspace_org(None, "T1") == "org_a"
    with pytest.raises(ValueError, match="another organization"):
        await bind_workspace(None, "T1", "org_b", "mallory")
    assert not await unbind_workspace(None, "T1", "org_b")
    assert await unbind_workspace(None, "T1", "org_a")
    assert await workspace_org(None, "T1") is None


def _client(user: str, org: str, roles: list[str]) -> TestClient:
    client = TestClient(app)
    client.cookies.set(
        SESSION_COOKIE_NAME, create_session_token(user_id=user, org_id=org, roles=roles)
    )
    return client


def test_pairing_requires_authentication_and_a_valid_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ROCKET_TRUST_PROXY_HEADERS", raising=False)
    app.state.slack_pairing_codes = PairingCodes()
    assert TestClient(app).post("/v1/slack/pair", json={"code": "ABCD2345"}).status_code == 401
    member = _client("member", "org_a", ["developer"])
    rejected = member.post("/v1/slack/pair", json={"code": "ABCD2345"})
    assert rejected.status_code == 400 and "invalid" in rejected.json()["detail"]


def test_admin_binds_the_connected_workspace_to_their_org(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ROCKET_TRUST_PROXY_HEADERS", raising=False)
    reset_memory_workspaces()

    class _Bot:
        team_id = "T_ACME"

    class _Bots:
        async def list(self, org_id: str) -> list[_Bot]:
            return [_Bot()]

    monkeypatch.setattr(app.state, "slack_bot_store", _Bots(), raising=False)
    admin = _client("boss", "org_a", ["admin", "developer"])
    member = _client("dev", "org_a", ["developer"])

    assert member.put("/v1/admin/slack/workspace").status_code == 403
    bound = admin.put("/v1/admin/slack/workspace").json()
    assert bound == {
        "connected_team_id": "T_ACME",
        "bound_org_id": "org_a",
        "bound_to_this_org": True,
    }

    rival = _client("evil", "org_b", ["admin", "developer"])
    conflict = rival.put("/v1/admin/slack/workspace")
    assert conflict.status_code == 409
    assert admin.delete("/v1/admin/slack/workspace").json()["bound_org_id"] is None


@pytest.fixture
async def admin_manager(require_database: Callable[[Exception, str], None]) -> AsyncIterator[Any]:
    if not ADMIN_URL:
        pytest.skip("ADMIN_DATABASE_URL not configured")
    manager = DatabaseSessionManager(ADMIN_URL)
    try:
        await manager.create_tables()
    except Exception as err:
        require_database(err, ADMIN_URL)
    yield manager
    await manager.close()


@pytest.mark.asyncio
async def test_member_lookup_is_scoped_to_the_workspace_org(admin_manager: Any) -> None:
    tag = uuid.uuid4().hex[:8]
    org_a, org_b = f"sl_org_a_{tag}", f"sl_org_b_{tag}"
    async with admin_manager.session() as session:
        for org in (org_a, org_b):
            session.add(OrganizationModel(id=org, name=org, slug=org.replace("_", "-")))
        await session.flush()
        session.add(
            UserModel(id=f"alice_{tag}", org_id=org_a, email=f"alice_{tag}@acme.io", name="Alice")
        )
        session.add(
            UserModel(id=f"bob_{tag}", org_id=org_b, email=f"alice_{tag}@other.io", name="Bob")
        )

    # Nothing is linked yet, and an e-mail match never crosses organizations.
    assert await find_member(admin_manager, "U1", org_a) is None
    assert await find_member(admin_manager, "U1", org_a, f"alice_{tag}@other.io") is None
    assert await find_member(admin_manager, "U1", org_a, f"alice_{tag}@acme.io") == (
        f"alice_{tag}",
        org_a,
    )

    # A confirmed link is found by Slack id - but only inside the member's own org.
    assert await link_member(admin_manager, f"alice_{tag}", org_a, "U1")
    assert await find_member(admin_manager, "U1", org_a) == (f"alice_{tag}", org_a)
    assert await find_member(admin_manager, "U1", org_b) is None
    # Linking into a different org's member fails.
    assert not await link_member(admin_manager, f"alice_{tag}", org_b, "U9")


def test_a_member_outside_the_signed_in_organization_is_told_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route used to say "needs the PostgreSQL store" whenever the member row was not
    found, which hid a member stranded in another organization."""
    from api.routers import slack_admin

    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ROCKET_TRUST_PROXY_HEADERS", raising=False)
    reset_memory_workspaces()

    class _Codes:
        async def complete(self, code: str) -> tuple[str, str]:
            return "U1", "T1"

    async def not_here(*_: Any) -> bool:
        return False

    app.state.slack_pairing_codes = _Codes()
    monkeypatch.setenv("ROCKET_AUTO_PROVISION_TENANTS", "false")
    monkeypatch.setattr(app.state, "db_manager", object(), raising=False)

    async def bound_here(*_: Any) -> str:
        return "org_a"

    monkeypatch.setattr(slack_admin, "link_member", not_here)
    monkeypatch.setattr(slack_admin, "workspace_org", bound_here)

    answer = _client("stranded", "org_a", ["developer"]).post(
        "/v1/slack/pair", json={"code": "ABCD2345"}
    )

    assert answer.status_code == 409
    assert "not a member of the organization" in answer.json()["detail"]
