"""``users`` has no row-level security, so every read of it must name the organization.

Two organizations on a real PostgreSQL: member counts, the first-member bootstrap and
sign-in never see, count or apply the other organization's rows.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from api.auth.roles import clear_role_cache
from api.db.models import UserModel
from api.db.session import DatabaseSessionManager
from api.main import app
from api.routers.auth import MemberOfAnotherOrganization, _ensure_db_user_and_org
from fastapi.testclient import TestClient

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="needs PostgreSQL (TEST_DATABASE_URL)")

ORG_A, ORG_B = "test_org", "usage_org"


@pytest.fixture
async def db() -> AsyncIterator[DatabaseSessionManager]:
    manager = DatabaseSessionManager(DATABASE_URL)
    try:
        yield manager
    finally:
        await manager.close()


@pytest.fixture(autouse=True)
def _no_admin_emails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)


def _user_id() -> str:
    return f"usr_scope_{uuid.uuid4().hex[:8]}"


async def _add_member(db: DatabaseSessionManager, org_id: str, role: str = "developer") -> str:
    user_id = _user_id()
    async with db.session(tenant_org_id=org_id) as session:
        session.add(
            UserModel(id=user_id, org_id=org_id, email=f"{user_id}@x.test", name="M", role=role)
        )
    return user_id


async def test_the_first_member_of_an_organization_is_its_admin_whatever_other_orgs_hold(
    db: DatabaseSessionManager,
) -> None:
    await _add_member(db, ORG_A)
    user_id = _user_id()
    roles = await _ensure_db_user_and_org(db, user_id, ORG_B, f"{user_id}@x.test", "First")
    assert "admin" in roles

    second = _user_id()
    assert await _ensure_db_user_and_org(db, second, ORG_B, f"{second}@x.test", "Second") == [
        "developer"
    ]


async def test_a_member_of_another_organization_is_refused_not_given_their_role(
    db: DatabaseSessionManager,
) -> None:
    admin_elsewhere = await _add_member(db, ORG_A, role="admin")
    with pytest.raises(MemberOfAnotherOrganization):
        await _ensure_db_user_and_org(db, admin_elsewhere, ORG_B, "a@x.test", "A")


@pytest.fixture
def client() -> Iterator[TestClient]:
    clear_role_cache()
    with TestClient(app) as c:
        yield c


async def test_the_member_list_counts_only_the_organization(
    db: DatabaseSessionManager, client: TestClient
) -> None:
    mine = await _add_member(db, ORG_A)
    for _ in range(3):
        await _add_member(db, ORG_B)
    headers = {"X-Tenant-Org-Id": ORG_A, "X-Tenant-User-Id": mine, "X-User-Role": "admin"}

    page = client.get("/v1/admin/users", headers=headers).json()

    assert {u["id"] for u in page["users"]} == {mine}
    assert page["total"] == 1
