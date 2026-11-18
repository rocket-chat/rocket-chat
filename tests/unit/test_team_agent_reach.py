"""A team's agent can be opened beyond its team, and brings its team's skills, MCP servers and
knowledge bases to whoever it serves: a Slack bot's public threads run as guests, who belong to
no team."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from api.main import app
from api.models import AgentPersonaModel
from api.services import mcp_turn
from api.services.agent_access import Principal, may_use, reset_memory_grants, set_grants
from api.services.audit import AuditService
from api.services.personas import list_personas, save_persona
from api.services.proposals import BoundProposals
from api.services.settings import reset_memory_settings
from api.services.skills_lookup import _memory_skills
from api.services.teams import create_team, set_team_member
from api.turns import runner
from fastapi.testclient import TestClient
from turn_harness import run_turn

from specifications.interfaces.proposals import ProposalEdit

GUEST = Principal("slack:T1", ("guest",))
OUTSIDER = Principal("bob", ("member",))


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    reset_memory_grants()
    reset_memory_settings()
    _memory_skills.clear()
    mcp_turn._mcp_servers_store.clear()
    yield
    _memory_skills.clear()
    mcp_turn._mcp_servers_store.clear()


async def _team_agent(org: str, **fields: Any) -> tuple[str, str]:
    team = await create_team(None, org, "Data", "", "root")
    await set_team_member(None, org, team.id, "alice", "admin")
    agent = await save_persona(
        None,
        org,
        "root",
        True,
        AgentPersonaModel(
            id=f"agent-{org}",
            name="Data assistant",
            role_title="Data",
            system_prompt="DATA-MARKER",
            scope="team",
            team_id=team.id,
            **fields,
        ),
    )
    return team.id, agent.id


async def test_a_team_agent_is_its_teams_until_its_administrators_grant_more() -> None:
    org = "reach_access"
    team_id, agent_id = await _team_agent(org)
    agent = AgentPersonaModel(
        id=agent_id, name="x", role_title="x", system_prompt="x", scope="team", team_id=team_id
    )
    member = Principal("alice", ("member",), ((team_id, "admin"),))

    assert await may_use(None, org, member, agent)
    assert not await may_use(None, org, GUEST, agent)
    assert not await may_use(None, org, OUTSIDER, agent)

    await set_grants(None, org, agent_id, [("guests", "")], "alice")
    assert await may_use(None, org, GUEST, agent)
    assert not await may_use(None, org, OUTSIDER, agent), "guests only, not every member"
    assert await may_use(None, org, member, agent)

    await set_grants(None, org, agent_id, [("everyone", "")], "alice")
    assert await may_use(None, org, OUTSIDER, agent)
    assert not await may_use(None, org, GUEST, agent)


async def test_a_guests_turn_with_a_team_agent_reaches_the_teams_skills_and_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = "reach_turn"
    team_id, agent_id = await _team_agent(org, skill_ids=["sk-traps"])
    await set_grants(None, org, agent_id, [("guests", "")], "alice")
    _memory_skills.setdefault(org, {})["sk-traps"] = {
        "name": "warehouse-traps",
        "description": "Which table lies",
        "body": "TRAPS-BODY",
        "scope": "team",
        "team_id": team_id,
        "is_active": True,
    }
    asked: list[tuple[Any, ...]] = []
    real = runner._mcp_tools_for_turn

    async def recording(*args: Any) -> Any:
        asked.append(args)
        return await real(*args)

    monkeypatch.setattr(runner, "_mcp_tools_for_turn", recording)

    outcome = await run_turn(
        org,
        user="slack:T1",
        metadata={"agent_id": agent_id},
        script=[("execute_skill", {"skill_name": "warehouse-traps", "arguments": {}})],
    )

    first = outcome.model.requests[0]
    system = "\n".join(str(m.content) for m in first.messages if m.role == "system")
    assert "DATA-MARKER" in system and "`warehouse-traps`: Which table lies" in system
    last = [r for r in outcome.model.requests if r.tools][-1]
    results = "\n".join(str(m.content) for m in last.messages if m.role == "tool")
    assert "TRAPS-BODY" in results
    assert asked and asked[0][2:4] == ("slack:T1", team_id)


async def test_a_team_server_is_reached_through_the_teams_agent_only() -> None:
    org = "reach_mcp"
    mcp_turn._mcp_servers_store["bq"] = {
        "id": "bq",
        "org_id": org,
        "name": "Warehouse",
        "transport": "http",
        "endpoint_or_command": "https://mcp.example.com/mcp",
        "scope": "team",
        "team_id": "team_data",
        "encrypted_auth": {},
        "tools": [],
    }

    other_agent = await mcp_turn._load_rows(None, org, "slack:T1", [], None)
    other_team = await mcp_turn._load_rows(None, org, "slack:T1", [], "team_other")
    data_agent = await mcp_turn._load_rows(None, org, "slack:T1", [], "team_data")

    assert other_agent == [] and other_team == []
    assert [row["id"] for row in data_agent] == ["bq"]


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        app.state.db_manager = None
        yield c


def _as(role: str, user: str, org: str) -> dict[str, str]:
    return {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": user, "X-User-Role": role}


def test_the_teams_administrators_decide_who_else_may_use_its_agent(client: TestClient) -> None:
    org = "reach_route"
    team_id, agent_id = client.portal.call(_team_agent, org)  # type: ignore[union-attr]
    client.portal.call(set_team_member, None, org, team_id, "carol", "member")  # type: ignore[union-attr]
    body = {"grants": [{"principal_kind": "guests"}]}

    by_member = client.put(
        f"/v1/agents/{agent_id}/access", json=body, headers=_as("member", "carol", org)
    )
    by_outsider = client.put(
        f"/v1/agents/{agent_id}/access", json=body, headers=_as("member", "bob", org)
    )
    by_team_admin = client.put(
        f"/v1/agents/{agent_id}/access", json=body, headers=_as("member", "alice", org)
    )

    assert by_member.status_code == 403
    assert by_outsider.status_code == 404, "an outsider does not see the team's agent"
    assert by_team_admin.status_code == 200, by_team_admin.text
    read = client.get(f"/v1/agents/{agent_id}/access", headers=_as("member", "alice", org))
    assert read.json()["grants"] == [{"principal_kind": "guests", "principal_id": ""}]


async def test_the_picker_lists_a_team_agent_to_whom_its_grants_name_only() -> None:
    org = "reach_list"
    _, agent_id = await _team_agent(org)

    assert agent_id not in {p.id for p in await list_personas(None, org, "bob", ["member"])}
    await set_grants(None, org, agent_id, [("everyone", "")], "alice")

    assert agent_id in {p.id for p in await list_personas(None, org, "bob", ["member"])}
    assert agent_id in {p.id for p in await list_personas(None, org, "alice", ["member"])}
    assert [p.id for p in await list_personas(None, org, "alice", ["member"])].count(agent_id) == 1


async def test_a_team_agent_proposes_changes_to_its_teams_skills() -> None:
    org = "reach_propose"
    team_id, agent_id = await _team_agent(org, skill_ids=["sk-traps"], proposes_changes=True)
    _memory_skills.setdefault(org, {})["sk-traps"] = {
        "name": "warehouse-traps",
        "description": "Which table lies",
        "body": "Use dbt models only.",
        "scope": "team",
        "team_id": team_id,
        "is_active": True,
    }
    proposals = BoundProposals(
        db_mgr=None,
        org_id=org,
        agent_id=agent_id,
        session_id="s1",
        requested_by="slack:<@U1>",
        audit=AuditService(None),
    )

    outcome = await proposals.propose(
        "skill",
        "warehouse-traps",
        [ProposalEdit("Use dbt models only.", "Use dbt models only; archive tables are stale.")],
        "Stale archive",
        "A reviewer said so.",
    )

    assert "waiting for review" in outcome, outcome
