"""Invitations are each organization's own, under forced Row-Level Security."""

from __future__ import annotations

import time

from api.db.models import OrgInviteModel
from api.db.session import DatabaseSessionManager
from api.services.memberships import list_invites, revoke_invite

from tests.support.rls import (
    TEST_DATABASE_URL,
    add_row,
    assert_cannot_write_into,
    assert_isolated,
    needs_postgres,
    new_id,
    two_orgs,
)


def _invite(org_id: str) -> OrgInviteModel:
    now = int(time.time())
    return OrgInviteModel(
        id=new_id("inv"),
        org_id=org_id,
        email="new@x.test",
        role="member",
        token_hash="0" * 64,
        expires_at=now + 86_400,
        created_by=f"u_{org_id}",
        created_at=now,
    )


@needs_postgres
async def test_invitations_are_isolated_in_postgres() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org_a, org_b = await two_orgs(db)
        invite = _invite(org_a)
        await add_row(db, org_a, invite)

        await assert_isolated(db, OrgInviteModel, org_a, org_b)
        assert await list_invites(db, org_b) == []
        assert not await revoke_invite(db, org_b, invite.id), "another organization cannot revoke"
        assert [i.id for i in await list_invites(db, org_a)] == [invite.id]
        await assert_cannot_write_into(db, org_b, _invite(org_a))
    finally:
        await db.close()
