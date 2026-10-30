"""People in several organizations, on a real PostgreSQL (P0.2, P2.5).

``users`` is the global identity; a role lives in ``memberships`` (forced RLS). Sign-in, member
lists, invitations and switching never see, count or apply another organization's rows.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from api.auth.roles import clear_role_cache
from api.auth.session_manager import create_session_token
from api.db.models import MembershipModel, OrgInviteModel, UserModel
from api.db.session import DatabaseSessionManager
from api.main import app
from api.routers.auth import _ensure_db_user_and_org
from api.services.memberships import (
    InviteInvalid,
    accept_invite,
    create_invite,
    ensure_membership,
    memberships_of,
)
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="needs PostgreSQL (TEST_DATABASE_URL)")


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


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _org_with_member(db: DatabaseSessionManager, role: str = "member") -> tuple[str, str]:
    org_id, user_id = _id("org"), _id("usr")
    await _ensure_db_user_and_org(db, _id("usr_seed"), org_id, f"{_id('s')}@x.test", "Seed")
    async with db.session(tenant_org_id=org_id) as session:
        session.add(
            UserModel(id=user_id, org_id=org_id, email=f"{user_id}@x.test", name="M", role=role)
        )
        await session.flush()
        await ensure_membership(session, user_id, org_id, role)
    return org_id, user_id


async def test_the_first_member_of_an_organization_is_its_admin(db: DatabaseSessionManager) -> None:
    org_id = _id("org")
    first = _id("usr")
    _, roles = await _ensure_db_user_and_org(db, first, org_id, f"{first}@x.test", "First")
    assert "admin" in roles
    second = _id("usr")
    assert (await _ensure_db_user_and_org(db, second, org_id, f"{second}@x.test", "Second"))[1] == [
        "member"
    ]


async def test_an_admin_elsewhere_becomes_a_plain_member_here(db: DatabaseSessionManager) -> None:
    org_a, admin_of_a = await _org_with_member(db, role="admin")
    org_b, _ = await _org_with_member(db)
    _, roles = await _ensure_db_user_and_org(db, admin_of_a, org_b, f"{admin_of_a}@x.test", "A")
    assert roles == ["member"]
    found = {m.org_id: m.role for m in await memberships_of(db, admin_of_a)}
    assert found == {org_a: "admin", org_b: "member"}


async def test_an_organization_sees_only_its_memberships(db: DatabaseSessionManager) -> None:
    org_a, member_a = await _org_with_member(db)
    org_b, _ = await _org_with_member(db)
    async with db.session(tenant_org_id=org_b) as session:
        rows = (await session.execute(select(MembershipModel.org_id))).scalars().all()
    assert set(rows) == {org_b}
    # A person reads their own rows across organizations, and nothing else.
    async with db.session(tenant_user_id=member_a) as session:
        rows = (await session.execute(select(MembershipModel.org_id))).scalars().all()
    assert set(rows) == {org_a}


async def test_a_membership_cannot_be_written_into_another_organization(
    db: DatabaseSessionManager,
) -> None:
    org_a, member_a = await _org_with_member(db)
    org_b, _ = await _org_with_member(db)
    with pytest.raises(DBAPIError):
        async with db.session(tenant_org_id=org_a, tenant_user_id=member_a) as session:
            session.add(
                MembershipModel(
                    id=_id("mbr"), user_id=member_a, org_id=org_b, role="admin", created_at=0
                )
            )


async def test_an_invitation_works_once_for_its_address_until_it_expires(
    db: DatabaseSessionManager,
) -> None:
    org_a, admin = await _org_with_member(db, role="admin")
    _, invitee = await _org_with_member(db)
    _, token = await create_invite(db, org_a, f"{invitee}@x.test", "member", admin, 7)
    with pytest.raises(InviteInvalid):
        await accept_invite(db, token, invitee, "someone-else@x.test")
    joined = await accept_invite(db, token, invitee, f"{invitee}@x.test")
    assert (joined.org_id, joined.role) == (org_a, "member")
    with pytest.raises(InviteInvalid):
        await accept_invite(db, token, invitee, f"{invitee}@x.test")

    invite, expired = await create_invite(db, org_a, "late@x.test", "viewer", admin, 1)
    async with db.session(tenant_org_id=org_a) as session:
        await session.execute(
            update(OrgInviteModel)
            .where(OrgInviteModel.id == invite.id)
            .values(expires_at=int(time.time()) - 1)
        )
    with pytest.raises(InviteInvalid, match="expired"):
        await accept_invite(db, expired, invitee, "late@x.test")


@pytest.fixture
def client() -> Iterator[TestClient]:
    clear_role_cache()
    with TestClient(app) as c:
        yield c


def _cookie(user_id: str, org_id: str) -> dict[str, str]:
    return {
        "rocket_session": create_session_token(user_id=user_id, org_id=org_id, roles=["member"])
    }


async def test_switching_needs_a_membership_and_the_list_is_the_active_organization(
    db: DatabaseSessionManager, client: TestClient
) -> None:
    org_a, person = await _org_with_member(db)
    org_b, _ = await _org_with_member(db)
    org_c, _ = await _org_with_member(db)
    await _ensure_db_user_and_org(db, person, org_b, f"{person}@x.test", "P")

    switched = client.post(
        "/v1/auth/switch-org", json={"org_id": org_b}, cookies=_cookie(person, org_a)
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["org_id"] == org_b
    refused = client.post(
        "/v1/auth/switch-org", json={"org_id": org_c}, cookies=_cookie(person, org_a)
    )
    assert refused.status_code == 403

    listed = client.get("/v1/me/organizations", cookies=_cookie(person, org_b)).json()[
        "organizations"
    ]
    assert {o["id"] for o in listed} == {org_a, org_b}
    assert [o["id"] for o in listed if o["active"]] == [org_b]


async def test_a_session_token_for_an_organization_left_grants_nothing(
    db: DatabaseSessionManager, client: TestClient
) -> None:
    org_a, person = await _org_with_member(db)
    _, other = await _org_with_member(db)
    stale = client.get("/v1/me/access", cookies=_cookie(other, org_a))
    assert stale.status_code == 200
    assert stale.json()["capabilities"] == []
    member = client.get("/v1/me/access", cookies=_cookie(person, org_a)).json()
    assert member["role"] == "member"


async def test_the_member_list_counts_only_the_organization(
    db: DatabaseSessionManager, client: TestClient
) -> None:
    org_a, mine = await _org_with_member(db, role="admin")
    org_b, _ = await _org_with_member(db)
    headers = {"X-Tenant-Org-Id": org_a, "X-Tenant-User-Id": mine, "X-User-Role": "admin"}
    page = client.get("/v1/admin/users", headers=headers).json()
    assert mine in {u["id"] for u in page["users"]}
    assert all(u["id"] not in (org_b,) for u in page["users"])
    assert page["total"] == len(page["users"])
