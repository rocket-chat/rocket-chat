"""Who may use an agent is each organization's own, under forced Row-Level Security."""

from __future__ import annotations

from api.db.models import AgentGrantModel
from api.db.session import DatabaseSessionManager
from api.services.agent_access import grants_for

from tests.support.rls import (
    TEST_DATABASE_URL,
    add_row,
    assert_cannot_write_into,
    assert_isolated,
    needs_postgres,
    new_id,
    two_orgs,
)


def _grant(org_id: str, agent_id: str) -> AgentGrantModel:
    return AgentGrantModel(
        id=new_id("grant"),
        org_id=org_id,
        agent_id=agent_id,
        principal_kind="member",
        principal_id=f"u_{org_id}",
        created_by=f"u_{org_id}",
    )


@needs_postgres
async def test_agent_grants_are_isolated_in_postgres() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org_a, org_b = await two_orgs(db)
        agent_id = new_id("agent")
        await add_row(db, org_a, _grant(org_a, agent_id))

        await assert_isolated(db, AgentGrantModel, org_a, org_b)
        assert await grants_for(db, org_b, agent_id) == []
        assert [g.principal_id for g in await grants_for(db, org_a, agent_id)] == [f"u_{org_a}"]
        await assert_cannot_write_into(db, org_b, _grant(org_a, agent_id))
    finally:
        await db.close()
