"""Integration tests for organization user administration (admin Users & Roles page).

Covers listing members, changing a role, rejecting an invalid role, refusing to demote the
last administrator, and the admin-only gate — against the real ASGI surface and Postgres.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from api.db.models import OrganizationModel, UserModel
from api.db.session import DatabaseSessionManager
from api.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

ADMIN_DB_URL = os.getenv("ADMIN_DATABASE_URL", "")
TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")


def _headers(org: str, user: str, role: str) -> dict[str, str]:
    return {
        "X-Tenant-Org-Id": org,
        "X-Tenant-User-Id": user,
        "X-User-Role": role,
    }


@pytest.fixture
async def client(
    require_database: Callable[[Exception, str], None],
) -> AsyncIterator[tuple[AsyncClient, str]]:
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not configured")
    app_manager = DatabaseSessionManager(TEST_DB_URL)
    admin_manager = DatabaseSessionManager(ADMIN_DB_URL or TEST_DB_URL)
    org_id = f"org_adm_{uuid.uuid4().hex[:8]}"
    try:
        async with admin_manager.session() as session:
            session.add(OrganizationModel(id=org_id, name="Admin Test", slug=org_id))
            session.add(
                UserModel(
                    id="u_admin", org_id=org_id, email="admin@test", name="Admin", role="admin"
                )
            )
            session.add(
                UserModel(id="u_dev", org_id=org_id, email="dev@test", name="Dev", role="developer")
            )
            await session.flush()
    except Exception as err:
        require_database(err, TEST_DB_URL)

    original = getattr(app.state, "db_manager", None)
    app.state.db_manager = app_manager
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as async_client:
            yield async_client, org_id
    finally:
        app.state.db_manager = original
        async with admin_manager.session() as session:
            await session.execute(text("DELETE FROM audit_events WHERE org_id = :o"), {"o": org_id})
            await session.execute(text("DELETE FROM users WHERE org_id = :o"), {"o": org_id})
            await session.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org_id})
        await app_manager.close()
        await admin_manager.close()


async def test_lists_org_members(client: tuple[AsyncClient, str]) -> None:
    http, org = client
    response = await http.get("/v1/admin/users", headers=_headers(org, "u_admin", "admin"))
    assert response.status_code == 200, response.text
    emails = {u["email"] for u in response.json()["users"]}
    assert emails == {"admin@test", "dev@test"}


async def test_changes_a_members_role(client: tuple[AsyncClient, str]) -> None:
    http, org = client
    response = await http.patch(
        "/v1/admin/users/u_dev",
        json={"role": "readonly"},
        headers=_headers(org, "u_admin", "admin"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["role"] == "readonly"
    assert "reload" in body["note"].lower()


@pytest.mark.parametrize("role", ["superuser", "guest"])
async def test_rejects_an_invalid_or_internal_role(
    client: tuple[AsyncClient, str], role: str
) -> None:
    http, org = client
    response = await http.patch(
        "/v1/admin/users/u_dev",
        json={"role": role},
        headers=_headers(org, "u_admin", "admin"),
    )
    assert response.status_code == 422


async def test_cannot_demote_the_last_administrator(client: tuple[AsyncClient, str]) -> None:
    http, org = client
    response = await http.patch(
        "/v1/admin/users/u_admin",
        json={"role": "developer"},
        headers=_headers(org, "u_admin", "admin"),
    )
    assert response.status_code == 409


async def test_non_admin_cannot_list_or_change(client: tuple[AsyncClient, str]) -> None:
    http, org = client
    headers = _headers(org, "u_dev", "developer")
    assert (await http.get("/v1/admin/users", headers=headers)).status_code == 403
    assert (
        await http.patch("/v1/admin/users/u_dev", json={"role": "readonly"}, headers=headers)
    ).status_code == 403
