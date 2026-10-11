"""Password sign-in for self-hosted installs without an identity provider (opt-in, hardened)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from api.auth import local
from api.auth.local import (
    AddressThrottle,
    WeakPassword,
    check_password_policy,
    hash_password,
    verify_password,
)
from api.main import app
from fastapi.testclient import TestClient

ADMIN_HEADERS = {
    "X-Tenant-Org-Id": "default_org",
    "X-Tenant-User-Id": "root",
    "X-User-Role": "admin",
}
MEMBER_HEADERS = {**ADMIN_HEADERS, "X-User-Role": "developer"}
STRONG = "correct horse battery staple"


def test_the_password_policy_rejects_weak_passwords() -> None:
    for weak, why in [
        ("short", "at least"),
        ("a" * 200, "at most"),
        ("aaaaaaaaaaaaaaaa", "repetitive"),
        ("alice@acme.test", "e-mail"),
        ("my-alice-is-secret!", "e-mail"),
    ]:
        with pytest.raises(WeakPassword, match=why):
            check_password_policy(weak, "alice@acme.test")
    check_password_policy(STRONG, "alice@acme.test")


def test_hashes_are_argon2id_salted_and_verified_without_leaking() -> None:
    first, second = hash_password(STRONG), hash_password(STRONG)

    assert first.startswith("$argon2id$") and first != second
    assert verify_password(first, STRONG) and not verify_password(first, STRONG + "x")
    assert not verify_password(None, STRONG), "unknown account never verifies"
    assert not verify_password("not-a-hash", STRONG)


def test_the_address_throttle_is_a_sliding_window() -> None:
    throttle = AddressThrottle(limit=3, window_seconds=60)

    assert [throttle.hit("1.2.3.4", now=t) for t in (0, 1, 2, 3)] == [True, True, True, False]
    assert throttle.hit("5.6.7.8", now=3), "other addresses are unaffected"
    assert throttle.hit("1.2.3.4", now=61), "the window slides"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("local accounts need PostgreSQL")
    monkeypatch.setenv("LOCAL_AUTH_ENABLED", "true")
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", f"root-{uuid.uuid4().hex[:6]}@acme.test")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", STRONG)
    monkeypatch.setattr(local, "IP_ATTEMPTS_PER_MINUTE", 1000)
    from api.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "_login_throttle", AddressThrottle(limit=1000))
    with TestClient(app, base_url="https://rocket.test") as c:
        yield c


def _login(client: TestClient, email: str, password: str) -> object:
    return client.post("/api/auth/local/login", json={"email": email, "password": password})


def test_the_initial_admin_signs_in_and_gets_a_session(client: TestClient) -> None:
    email = os.environ["INITIAL_ADMIN_EMAIL"]

    res = _login(client, email, STRONG)

    assert res.status_code == 200 and res.json()["must_change_password"] is False  # type: ignore[attr-defined]
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == email and "admin" in me.json()["roles"]
    assert client.get("/api/auth/configured-providers").json()["local"] is True


def test_failures_do_not_reveal_whether_an_account_exists(client: TestClient) -> None:
    email = os.environ["INITIAL_ADMIN_EMAIL"]

    wrong_password = _login(client, email, "not the password at all")
    unknown_user = _login(client, "nobody@acme.test", "not the password at all")

    assert wrong_password.status_code == unknown_user.status_code == 401  # type: ignore[attr-defined]
    assert wrong_password.json() == unknown_user.json()  # type: ignore[attr-defined]


def test_repeated_failures_lock_the_account_even_for_the_right_password(client: TestClient) -> None:
    email = os.environ["INITIAL_ADMIN_EMAIL"]
    for _ in range(local.MAX_FAILED_ATTEMPTS):
        assert _login(client, email, "guess-guess-guess").status_code == 401  # type: ignore[attr-defined]

    locked = _login(client, email, STRONG)

    assert locked.status_code == 429  # type: ignore[attr-defined]
    assert int(locked.headers["retry-after"]) > 0  # type: ignore[attr-defined]


def test_an_admin_creates_a_member_who_must_change_the_temporary_password(
    client: TestClient,
) -> None:
    email = f"dev-{uuid.uuid4().hex[:6]}@acme.test"
    created = client.post(
        "/v1/admin/users",
        json={"email": email, "name": "Dev", "role": "developer"},
        headers=ADMIN_HEADERS,
    )
    assert created.status_code == 200, created.text
    temporary = created.json()["temporary_password"]

    first = _login(client, email, temporary)
    assert first.json()["must_change_password"] is True  # type: ignore[attr-defined]

    new_password = "a brand new passphrase 42"
    weak = client.post(
        "/api/auth/local/password", json={"current_password": temporary, "new_password": "short"}
    )
    wrong = client.post(
        "/api/auth/local/password",
        json={"current_password": "wrong password here", "new_password": new_password},
    )
    changed = client.post(
        "/api/auth/local/password",
        json={"current_password": temporary, "new_password": new_password},
    )
    assert (weak.status_code, wrong.status_code, changed.status_code) == (422, 400, 200)

    assert _login(client, email, temporary).status_code == 401  # type: ignore[attr-defined]
    assert _login(client, email, new_password).json()["must_change_password"] is False  # type: ignore[attr-defined]


def test_resetting_a_password_unlocks_the_account(client: TestClient) -> None:
    email = f"locked-{uuid.uuid4().hex[:6]}@acme.test"
    user = client.post(
        "/v1/admin/users",
        json={"email": email, "name": "L", "role": "developer"},
        headers=ADMIN_HEADERS,
    ).json()["user"]
    for _ in range(local.MAX_FAILED_ATTEMPTS):
        _login(client, email, "wrong wrong wrong")
    assert _login(client, email, "wrong wrong wrong").status_code == 429  # type: ignore[attr-defined]

    reset = client.post(f"/v1/admin/users/{user['id']}/reset-password", headers=ADMIN_HEADERS)

    assert reset.status_code == 200
    assert _login(client, email, reset.json()["temporary_password"]).status_code == 200  # type: ignore[attr-defined]


def test_only_administrators_manage_local_accounts(client: TestClient) -> None:
    body = {"email": f"x-{uuid.uuid4().hex[:6]}@acme.test", "name": "X", "role": "developer"}

    assert client.post("/v1/admin/users", json=body, headers=MEMBER_HEADERS).status_code == 403
    assert (
        client.post("/v1/admin/users/someone/reset-password", headers=MEMBER_HEADERS).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/admin/users", json={**body, "role": "emperor"}, headers=ADMIN_HEADERS
        ).status_code
        == 422
    )
    first = client.post("/v1/admin/users", json=body, headers=ADMIN_HEADERS)
    duplicate = client.post("/v1/admin/users", json=body, headers=ADMIN_HEADERS)
    assert first.status_code == 200 and duplicate.status_code == 409


def test_sso_members_have_no_password_and_cannot_use_this_door(client: TestClient) -> None:
    import asyncio

    from api.db.models import UserModel
    from api.db.session import DatabaseSessionManager

    email = f"sso-{uuid.uuid4().hex[:6]}@acme.test"

    async def seed() -> None:
        manager = DatabaseSessionManager(os.environ["TEST_DATABASE_URL"])
        async with manager.session(tenant_org_id="default_org") as session:
            session.add(
                UserModel(
                    id=f"usr_{uuid.uuid4().hex[:8]}", org_id="default_org", email=email, name="SSO"
                )
            )
        await manager.close()

    asyncio.run(seed())

    assert _login(client, email, STRONG).status_code == 401  # type: ignore[attr-defined]


def test_failed_and_successful_logins_are_audited(client: TestClient) -> None:
    email = os.environ["INITIAL_ADMIN_EMAIL"]
    _login(client, email, "wrong wrong wrong")
    _login(client, email, STRONG)

    audit = client.get("/v1/admin/audit?limit=50", headers=ADMIN_HEADERS)

    assert audit.status_code == 200
    summaries = [e.get("result_summary") for e in audit.json().get("events", [])]
    assert "failed" in summaries and "signed in with a password" in summaries


def test_password_sign_in_is_off_unless_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_AUTH_ENABLED", raising=False)
    with TestClient(app) as c:
        assert c.get("/api/auth/configured-providers").json()["local"] is False
        res = c.post("/api/auth/local/login", json={"email": "a@b.co", "password": STRONG})
        assert res.status_code == 403
