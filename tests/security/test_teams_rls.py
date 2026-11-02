"""Teams and their members are each organization's own, under forced Row-Level Security, and a
team's objects reach its members only (the ownership filter on real PostgreSQL)."""

from __future__ import annotations

import time

from api.db.models import (
    AgentPersonaEntityModel,
    TeamMembershipModel,
    TeamModel,
    UserModel,
)
from api.db.session import DatabaseSessionManager
from api.services.memberships import ensure_membership
from api.services.ownership import visible_clause
from api.services.teams import (
    TeamError,
    create_team,
    delete_team,
    set_team_member,
    teams_of,
)
from sqlalchemy import select

from specifications.interfaces.ownership import Viewer
from tests.support.rls import (
    TEST_DATABASE_URL,
    add_row,
    assert_cannot_write_into,
    assert_isolated,
    needs_postgres,
    new_id,
    two_orgs,
)


def _team(org: str, team_id: str | None = None) -> TeamModel:
    return TeamModel(id=team_id or new_id("team"), org_id=org, name=new_id("t"))


def _membership(org: str, team_id: str) -> TeamMembershipModel:
    return TeamMembershipModel(
        id=new_id("tm"), org_id=org, team_id=team_id, user_id=f"u_{org}", role="member"
    )


def _agent(org: str, **owner: str | None) -> AgentPersonaEntityModel:
    now = int(time.time())
    return AgentPersonaEntityModel(
        id=new_id("agent"), org_id=org, name="a", role_title="r", system_prompt="p", model="",
        created_at=now, updated_at=now, **owner,
    )  # fmt: skip


@needs_postgres
async def test_teams_and_their_members_are_isolated_in_postgres() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org_a, org_b = await two_orgs(db)
        team_id = new_id("team")
        await add_row(db, org_a, _team(org_a, team_id))
        await assert_isolated(db, TeamModel, org_a, org_b)
        await assert_cannot_write_into(db, org_b, _team(org_a))
        await add_row(db, org_a, _membership(org_a, team_id))
        await assert_isolated(db, TeamMembershipModel, org_a, org_b)
        await assert_cannot_write_into(db, org_b, _membership(org_a, team_id))
        assert await teams_of(db, org_b, f"u_{org_a}") == {}
    finally:
        await db.close()


@needs_postgres
async def test_a_team_object_is_visible_to_its_members_only_and_pins_the_team() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org, _ = await two_orgs(db)
        outsider = f"x_{org}"
        async with db.session(tenant_org_id=org) as session:
            session.add(
                UserModel(
                    id=outsider,
                    org_id=org,
                    email=f"{outsider}@t.io",
                    name="X",
                    created_at=int(time.time()),
                )
            )
            await session.flush()
            await ensure_membership(session, f"u_{org}", org, "member")
            await ensure_membership(session, outsider, org, "member")
        team = await create_team(db, org, "Platform", "", f"u_{org}")
        await set_team_member(db, org, team.id, f"u_{org}", "member")
        team_agent = _agent(org, scope="team", team_id=team.id)
        await add_row(db, org, team_agent)

        async def visible_to(viewer: Viewer) -> set[str]:
            async with db.session(tenant_org_id=org) as session:
                rows = await session.execute(
                    select(AgentPersonaEntityModel.id).where(
                        visible_clause(AgentPersonaEntityModel, viewer)
                    )
                )
                return {row[0] for row in rows}

        member = Viewer(f"u_{org}", teams=frozenset(await teams_of(db, org, f"u_{org}")))
        assert team_agent.id in await visible_to(member)
        assert team_agent.id not in await visible_to(Viewer(outsider))
        assert team_agent.id in await visible_to(Viewer(outsider, is_org_admin=True))

        try:
            await delete_team(db, org, team.id)
        except TeamError as err:
            assert err.status_code == 409
        else:
            raise AssertionError("a team that owns an agent was deleted")
    finally:
        await db.close()
