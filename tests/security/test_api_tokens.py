"""Personal API tokens: who they act as, for how long, and who can see or revoke them."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import Iterator

import pytest
from api.auth.api_tokens import (
    ApiTokens,
    format_token,
    guest_may_call,
    hash_secret,
    is_api_token,
    parse_token,
)
from api.auth.identity import resolve_token_identity
from api.auth.roles import clear_role_cache
from api.auth.session_manager import SESSION_COOKIE_NAME, create_session_token
from api.db.models import ApiTokenModel, MembershipModel, OrganizationModel, UserModel
from api.db.session import DatabaseSessionManager
from api.main import app
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from starlette.websockets import WebSocketDisconnect

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
needs_postgres = pytest.mark.skipif(not TEST_DATABASE_URL, reason="API tokens need PostgreSQL")


def test_a_token_names_its_organization_and_round_trips() -> None:
    token = format_token("acme_org", "0a1b2c3d4e5f6a7b", "s3cr3t-_x")

    parsed = parse_token(token)

    assert is_api_token(token) and token.startswith("rcpat_")
    assert parsed is not None
    assert (parsed.org_id, parsed.token_id, parsed.secret) == (
        "acme_org",
        "0a1b2c3d4e5f6a7b",
        "s3cr3t-_x",
    )


@pytest.mark.parametrize(
    "raw", ["", "rcpat_", "rcpat_a.b", "rcpat_a.b.c.d", "rcpat_!!!.id.secret", "Bearer x.y.z"]
)
def test_malformed_tokens_are_rejected(raw: str) -> None:
    assert parse_token(raw) is None


def test_only_a_hash_of_the_secret_is_kept() -> None:
    assert hash_secret("secret") != "secret" and len(hash_secret("secret")) == 64


def test_the_synchronous_resolver_never_accepts_an_api_token() -> None:
    assert resolve_token_identity(format_token("o", "id", "secret")) is None


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/agents"),
        ("POST", "/v1/sessions"),
        ("GET", "/v1/sessions/"),
        ("GET", "/v1/sessions/s1"),
        ("POST", "/v1/sessions/s1/turns"),
        ("DELETE", "/v1/sessions/s1/turns"),
        ("POST", "/v1/sessions/s1/questions/q1"),
        ("POST", "/v1/sessions/s1/approvals/a1"),
        ("POST", "/v1/sessions/s1/attachments"),
        ("GET", "/v1/sessions/s1/artifacts/a1/raw"),
    ],
)
def test_a_guest_token_may_hold_conversations(method: str, path: str) -> None:
    assert guest_may_call(method, path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/agents"),
        ("POST", "/v1/sessions/cleanup"),
        ("DELETE", "/v1/sessions/s1"),
        ("PATCH", "/v1/sessions/s1"),
        ("POST", "/v1/sessions/s1/share"),
        ("PUT", "/v1/sessions/s1/workspace/file"),
        ("POST", "/v1/sessions/s1/processes/p1/stop"),
        ("GET", "/v1/user/api-tokens"),
        ("GET", "/v1/admin/api-tokens"),
        ("GET", "/v1/settings"),
        ("GET", "/v1/sessions/s1/turns/../../../settings"),
    ],
)
def test_a_guest_token_may_not_call_anything_else(method: str, path: str) -> None:
    assert not guest_may_call(method, path)


def test_without_postgres_api_tokens_are_unavailable() -> None:
    tokens = ApiTokens(None)

    assert not tokens.available
    assert asyncio.run(tokens.authenticate(format_token("o", "id", "s"))) is None


# --- Against PostgreSQL --------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    if not TEST_DATABASE_URL:
        pytest.skip("API tokens need PostgreSQL")
    # As deployed: no development identity and no trusted proxy headers.
    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ROCKET_TRUST_PROXY_HEADERS", raising=False)
    with TestClient(app, base_url="https://rocket.test") as c:
        yield c


def _member(org: str, user: str) -> dict[str, str]:
    return {"Cookie": f"{SESSION_COOKIE_NAME}={create_session_token(user_id=user, org_id=org)}"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _write(statement: object, org: str | None = None) -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        async with db.session(tenant_org_id=org) as session:
            await session.execute(statement)  # type: ignore[call-overload]
    finally:
        await db.close()


def _set_role(user: str, role: str) -> None:
    """The member's role in their organization, which lives in ``memberships`` (decision D1)."""

    async def write() -> None:
        # The schema owner: a membership's row policy is per organization, unknown here.
        db = DatabaseSessionManager(os.getenv("ADMIN_DATABASE_URL") or TEST_DATABASE_URL)
        try:
            async with db.session() as session:
                await session.execute(
                    update(MembershipModel).where(MembershipModel.user_id == user).values(role=role)
                )
        finally:
            await db.close()

    asyncio.run(write())
    clear_role_cache()


def _create(client: TestClient, org: str, user: str, name: str = "ci") -> dict[str, object]:
    res = client.post("/v1/user/api-tokens", json={"name": name}, headers=_member(org, user))
    assert res.status_code == 201, res.text
    body: dict[str, object] = res.json()
    return body


def _ids() -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:8]
    return f"tok_org_{suffix}", f"alice_{suffix}", f"admin_{suffix}"


@needs_postgres
def test_a_token_acts_as_its_member_until_it_is_revoked(client: TestClient) -> None:
    org, alice, _ = _ids()
    created = _create(client, org, alice)
    token = str(created["token"])

    listed = client.get("/v1/user/api-tokens", headers=_bearer(token))
    me = client.get("/v1/sessions", headers=_bearer(token))

    assert token.startswith("rcpat_") and created["user_id"] == alice
    assert listed.status_code == 200 and [t["id"] for t in listed.json()] == [created["id"]]
    assert "token" not in listed.json()[0], "the secret is shown only once"
    assert listed.json()[0]["last_used_at"] is not None
    assert me.status_code == 200

    revoked = client.delete(f"/v1/user/api-tokens/{created['id']}", headers=_bearer(token))

    assert revoked.status_code == 200
    assert client.get("/v1/sessions", headers=_bearer(token)).status_code == 401


@needs_postgres
def test_a_wrong_secret_or_another_organization_is_refused(client: TestClient) -> None:
    org, alice, _ = _ids()
    other_org, mallory, _ = _ids()
    _create(client, other_org, mallory)
    parsed = parse_token(str(_create(client, org, alice)["token"]))
    assert parsed is not None

    wrong_secret = format_token(org, parsed.token_id, parsed.secret[:-1] + "x")
    moved = format_token(other_org, parsed.token_id, parsed.secret)

    assert client.get("/v1/sessions", headers=_bearer(wrong_secret)).status_code == 401
    assert client.get("/v1/sessions", headers=_bearer(moved)).status_code == 401


@needs_postgres
def test_an_expired_token_stops_working(client: TestClient) -> None:
    org, alice, _ = _ids()
    created = _create(client, org, alice)
    asyncio.run(
        _write(
            update(ApiTokenModel)
            .where(ApiTokenModel.id == created["id"])
            .values(expires_at=int(time.time()) - 1),
            org,
        )
    )

    assert client.get("/v1/sessions", headers=_bearer(str(created["token"]))).status_code == 401


@needs_postgres
def test_a_token_cannot_create_another_token(client: TestClient) -> None:
    org, alice, _ = _ids()
    token = str(_create(client, org, alice)["token"])

    res = client.post("/v1/user/api-tokens", json={"name": "child"}, headers=_bearer(token))

    assert res.status_code == 403


@needs_postgres
def test_a_token_carries_the_role_the_member_holds_now(client: TestClient) -> None:
    org, alice, _ = _ids()
    token = str(_create(client, org, alice)["token"])
    assert client.get("/v1/admin/api-tokens", headers=_bearer(token)).status_code == 403

    _set_role(alice, "admin")

    assert client.get("/v1/admin/api-tokens", headers=_bearer(token)).status_code == 200


@needs_postgres
def test_an_administrator_revokes_tokens_of_their_own_organization_only(
    client: TestClient,
) -> None:
    org, alice, admin = _ids()
    other_org, _, other_admin = _ids()
    created = _create(client, org, alice)
    _create(client, org, admin, "admin's own")
    _create(client, other_org, other_admin, "other org")
    _set_role(admin, "admin")
    _set_role(other_admin, "admin")

    seen_by_admin = client.get("/v1/admin/api-tokens", headers=_member(org, admin)).json()
    seen_elsewhere = client.get(
        "/v1/admin/api-tokens", headers=_member(other_org, other_admin)
    ).json()
    refused = client.delete(
        f"/v1/admin/api-tokens/{created['id']}", headers=_member(other_org, other_admin)
    )
    not_mine = client.delete(f"/v1/user/api-tokens/{created['id']}", headers=_member(org, admin))

    assert {t["user_id"] for t in seen_by_admin} == {alice, admin}
    assert created["id"] not in {t["id"] for t in seen_elsewhere}
    assert refused.status_code == 404 and not_mine.status_code == 404
    assert client.get("/v1/sessions", headers=_bearer(str(created["token"]))).status_code == 200

    revoked = client.delete(f"/v1/admin/api-tokens/{created['id']}", headers=_member(org, admin))

    assert revoked.status_code == 200
    assert client.get("/v1/sessions", headers=_bearer(str(created["token"]))).status_code == 401


@needs_postgres
def test_a_token_opens_the_session_websocket_with_the_query_parameter(
    client: TestClient,
) -> None:
    org, alice, _ = _ids()
    token = str(_create(client, org, alice)["token"])
    session_id = client.post(
        "/v1/sessions", json={"title": "scripted"}, headers=_bearer(token)
    ).json()["session_id"]

    with client.websocket_connect(f"/v1/sessions/{session_id}/ws?token={token}") as ws:
        assert ws.receive_json()["type"] == "sync"


def _create_guest(client: TestClient, org: str, admin: str) -> dict[str, object]:
    client.get("/v1/sessions", headers=_member(org, admin))  # provisions the admin
    _set_role(admin, "admin")
    res = client.post("/v1/admin/api-tokens", json={"name": "bot"}, headers=_member(org, admin))
    assert res.status_code == 201, res.text
    body: dict[str, object] = res.json()
    return body


@needs_postgres
def test_only_an_administrator_creates_a_guest_token(client: TestClient) -> None:
    org, alice, _ = _ids()
    client.get("/v1/sessions", headers=_member(org, alice))

    res = client.post("/v1/admin/api-tokens", json={"name": "bot"}, headers=_member(org, alice))

    assert res.status_code == 403


@needs_postgres
def test_a_guest_token_holds_its_own_conversations_and_nothing_else(client: TestClient) -> None:
    org, alice, admin = _ids()
    created = _create_guest(client, org, admin)
    guest = _bearer(str(created["token"]))
    private = client.post(
        "/v1/sessions", json={"title": "alice's", "is_shared": False}, headers=_member(org, alice)
    ).json()["session_id"]

    session = client.post("/v1/sessions", json={"title": "from a bot"}, headers=guest)
    listed = client.get("/v1/admin/api-tokens", headers=_member(org, admin)).json()

    assert created["guest"] is True and str(created["user_id"]).startswith("apitoken:")
    assert session.status_code == 200 and session.json()["tenant_user_id"] == created["user_id"]
    assert (
        client.get(f"/v1/sessions/{session.json()['session_id']}", headers=guest).status_code == 200
    )
    assert [t["guest"] for t in listed if t["id"] == created["id"]] == [True]
    assert client.get(f"/v1/sessions/{private}", headers=guest).status_code == 403
    assert client.get("/v1/settings", headers=guest).status_code == 403
    assert client.get("/v1/user/api-tokens", headers=guest).status_code == 403
    assert client.get("/v1/admin/api-tokens", headers=guest).status_code == 403
    assert (
        client.post(
            "/v1/agents", json={"name": "x", "system_prompt": "y"}, headers=guest
        ).status_code
        == 403
    )


@needs_postgres
def test_a_guest_token_keeps_the_guest_role_and_cannot_share_or_open_a_terminal(
    client: TestClient,
) -> None:
    org, _, admin = _ids()
    created = _create_guest(client, org, admin)
    token = str(created["token"])
    _set_role(str(created["user_id"]), "admin")
    session_id = client.post(
        "/v1/sessions", json={"title": "scripted"}, headers=_bearer(token)
    ).json()["session_id"]

    assert client.get("/v1/admin/api-tokens", headers=_bearer(token)).status_code == 403
    with client.websocket_connect(f"/v1/sessions/{session_id}/ws?token={token}") as ws:
        assert ws.receive_json()["type"] == "sync"
        ws.send_json({"type": "share", "is_shared": True, "collaborators": []})
        assert "only the owner or an admin" in ws.receive_json()["detail"]
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(f"/v1/sessions/{session_id}/terminal?token={token}") as ws,
    ):
        ws.receive_json()


@needs_postgres
def test_revoking_a_guest_token_keeps_its_conversations(client: TestClient) -> None:
    org, _, admin = _ids()
    created = _create_guest(client, org, admin)
    token = str(created["token"])
    session_id = client.post(
        "/v1/sessions", json={"title": "kept", "is_shared": False}, headers=_bearer(token)
    ).json()["session_id"]

    revoked = client.delete(f"/v1/admin/api-tokens/{created['id']}", headers=_member(org, admin))

    assert revoked.status_code == 200
    assert client.get("/v1/sessions", headers=_bearer(token)).status_code == 401
    assert client.get(f"/v1/sessions/{session_id}", headers=_member(org, admin)).status_code == 200


@needs_postgres
def test_tokens_are_isolated_by_row_level_security() -> None:
    org_a, alice, _ = _ids()
    org_b, _, _ = _ids()

    async def scenario() -> tuple[list[str], list[str]]:
        db = DatabaseSessionManager(TEST_DATABASE_URL)
        try:
            now = int(time.time())
            async with db.session(tenant_org_id=org_a) as session:
                session.add(OrganizationModel(id=org_a, name="A", slug=org_a, created_at=now))
                await session.flush()
                session.add(UserModel(id=alice, org_id=org_a, email=f"{alice}@a.test", name="A"))
                await session.flush()
                session.add(
                    ApiTokenModel(
                        id=uuid.uuid4().hex[:16],
                        org_id=org_a,
                        user_id=alice,
                        name="a",
                        secret_hash=hash_secret("s"),
                        created_at=now,
                        expires_at=now + 60,
                    )
                )
            seen: list[list[str]] = []
            for org in (org_a, org_b):
                async with db.session(tenant_org_id=org) as session:
                    rows = await session.execute(
                        select(ApiTokenModel.user_id).where(ApiTokenModel.user_id == alice)
                    )
                    seen.append(list(rows.scalars()))
            return seen[0], seen[1]
        finally:
            await db.close()

    own, other = asyncio.run(scenario())

    assert own == [alice] and other == []
