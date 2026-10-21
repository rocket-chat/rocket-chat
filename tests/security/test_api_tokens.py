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
    hash_secret,
    is_api_token,
    parse_token,
)
from api.auth.identity import resolve_token_identity
from api.auth.roles import clear_role_cache
from api.auth.session_manager import SESSION_COOKIE_NAME, create_session_token
from api.db.models import ApiTokenModel, OrganizationModel, UserModel
from api.db.session import DatabaseSessionManager
from api.main import app
from fastapi.testclient import TestClient
from sqlalchemy import select, update

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
    asyncio.run(_write(update(UserModel).where(UserModel.id == user).values(role=role)))
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
