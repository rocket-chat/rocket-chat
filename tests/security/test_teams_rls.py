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


@needs_postgres
async def test_a_team_key_is_the_teams_and_its_limits_go_with_it() -> None:
    from api.db.models import SpendLimitModel
    from api.services.model_credentials import ModelCredentialStore, NewCredential

    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org, other = await two_orgs(db)
        team = await create_team(db, org, "Platform", "", f"u_{org}")
        store = ModelCredentialStore(db)
        key = await store.create(
            org,
            NewCredential("team", None, "anthropic", "sk-team-pg-0001", "Team", team_id=team.id),
        )
        found = await store.default_for(org, "anthropic", owner_user_id=None, team_id=team.id)
        assert found is not None and found.id == key.id and found.team_id == team.id
        assert await store.default_for(org, "anthropic", owner_user_id=None) is None
        assert await store.get(other, key.id) is None
        await add_row(
            db,
            org,
            SpendLimitModel(
                id=new_id("lim"), org_id=org, subject_kind="team", subject_id=team.id,
                period="month", amount_usd=5.0, mode="block", created_by="x", team_id=team.id,
            ),
        )  # fmt: skip
        try:
            await delete_team(db, org, team.id)
        except TeamError as err:
            assert err.status_code == 409 and "credentials" in err.detail
        else:
            raise AssertionError("a team that owns a key was deleted")
        await store.delete(org, key.id)
        await delete_team(db, org, team.id)
        async with db.session(tenant_org_id=org) as session:
            left = await session.execute(
                select(SpendLimitModel).where(SpendLimitModel.subject_id == team.id)
            )
            assert left.scalars().all() == []
    finally:
        await db.close()


@needs_postgres
async def test_an_organization_agent_is_granted_to_a_team_in_postgres() -> None:
    from api.services.agent_access import grants_for, set_grants

    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org, _ = await two_orgs(db)
        team = await create_team(db, org, "Platform", "", f"u_{org}")
        agent_id = new_id("agent")
        await set_grants(db, org, agent_id, [("team", team.id)], f"u_{org}")
        assert [
            (g.principal_kind, g.principal_id) for g in await grants_for(db, org, agent_id)
        ] == [("team", team.id)]
    finally:
        await db.close()


@needs_postgres
async def test_a_teams_settings_and_sandbox_types_are_isolated_in_postgres() -> None:
    from api.db.models import TeamSettingModel
    from api.services.sandbox_catalogue import SandboxCatalogue
    from api.services.settings import load_team_config, save_team_config

    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org_a, org_b = await two_orgs(db)
        team = await create_team(db, org_a, "Platform", "", f"u_{org_a}")
        await save_team_config(db, org_a, team.id, "user_preferences", {"permission_mode": "plan"})
        assert await load_team_config(db, org_a, team.id, "user_preferences") == {
            "permission_mode": "plan"
        }
        await assert_isolated(db, TeamSettingModel, org_a, org_b)
        assert await load_team_config(db, org_b, team.id, "user_preferences") == {}
        catalogue = SandboxCatalogue(db)
        entry = await catalogue.create(org_a, name="Team box", image="img:1", team_id=team.id)
        teams = frozenset({team.id})
        assert await catalogue.resolve(org_a, sandbox_id=entry.id, teams=teams) is not None
        assert await catalogue.resolve(org_a, sandbox_id=entry.id) is None
        assert await catalogue.resolve(org_b, sandbox_id=entry.id, teams=teams) is None
        try:
            await delete_team(db, org_a, team.id)
        except TeamError as err:
            assert "sandboxes" in err.detail
        else:
            raise AssertionError("a team that owns a sandbox type was deleted")
    finally:
        await db.close()


@needs_postgres
async def test_the_session_list_shows_a_team_shared_session_to_its_members_in_postgres() -> None:
    from api.db.store import PostgresSessionStore

    from specifications.interfaces.session import SHARED_TEAMS_KEY, SessionRecord

    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org, _ = await two_orgs(db)
        store = PostgresSessionStore(db)
        session_id = new_id("sess")
        await store.create_session(
            SessionRecord(
                session_id=session_id,
                tenant_org_id=org,
                tenant_user_id=f"u_{org}",
                title="Plan",
                metadata={SHARED_TEAMS_KEY: ["team_platform"]},
            )
        )

        async def listed(teams: tuple[str, ...]) -> set[str]:
            page = await store.list_sessions_page(
                org, visible_to_user_id="someone_else", visible_to_teams=teams
            )
            return {record.session_id for record in page.records}

        assert session_id in await listed(("team_platform", "team_other"))
        assert session_id not in await listed(("team_other",))
        assert session_id not in await listed(())
    finally:
        await db.close()
