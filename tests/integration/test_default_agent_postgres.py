"""A member's default agent on PostgreSQL: the levels are read under each organization's
row-level security, and the team level counts only for a member of exactly one team."""

from __future__ import annotations

from api.db.session import DatabaseSessionManager
from api.models import AgentPersonaModel
from api.services.default_agent import resolve_default_agent
from api.services.memberships import ensure_membership
from api.services.personas import save_persona
from api.services.settings import save_org_config, save_team_config, save_user_config
from api.services.teams import create_team, set_team_member

from tests.support.rls import TEST_DATABASE_URL, needs_postgres, new_id, two_orgs


async def _agent(db: DatabaseSessionManager, org: str, admin: str) -> str:
    saved = await save_persona(
        db,
        org,
        admin,
        True,
        AgentPersonaModel(id=new_id("agent"), name="A", role_title="r", system_prompt="p"),
    )
    return saved.id


@needs_postgres
async def test_the_levels_resolve_on_postgres_and_stay_in_their_organization() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org, other = await two_orgs(db)
        member = f"u_{org}"
        async with db.session(tenant_org_id=org) as session:
            await ensure_membership(session, member, org, "member")
        org_agent, team_agent, own = [await _agent(db, org, member) for _ in range(3)]
        team = await create_team(db, org, "Platform", "", member)
        await set_team_member(db, org, team.id, member, "member")
        await save_org_config(db, org, "user_preferences", {"default_agent": org_agent}, [])
        await save_team_config(db, org, team.id, "user_preferences", {"default_agent": team_agent})

        choice = await resolve_default_agent(db, org, member, ["member"])
        assert (choice.agent_id, choice.source) == (team_agent, f"team:{team.id}")

        await save_user_config(db, org, member, "user_preferences", {"default_agent": own})
        assert (await resolve_default_agent(db, org, member, ["member"])).agent_id == own

        second = await create_team(db, org, "Second", "", member)
        await set_team_member(db, org, second.id, member, "member")
        inherited = await resolve_default_agent(db, org, member, ["member"], include_member=False)
        assert (inherited.agent_id, inherited.source) == (org_agent, "org")

        elsewhere = await resolve_default_agent(db, other, f"u_{other}", ["member"])
        assert (elsewhere.agent_id, elsewhere.source) == (None, "platform")
    finally:
        await db.close()
