"""A team's own defaults apply to its agents' turns, under the organization's locks and the
member's own choice; a team's sandbox types serve its members and agents only."""

from __future__ import annotations

from typing import Any

from api.models import AgentPersonaModel
from api.services.personas import save_persona
from api.services.sandbox_catalogue import SandboxCatalogue
from api.services.settings import save_org_config, save_team_config, save_user_config
from api.services.teams import create_team, set_team_member
from api.services.turn_service import permission_mode_for
from fastapi.testclient import TestClient

from tests.turn_harness import run_turn

ORG = "team_cfg"
ADMIN = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "root", "X-User-Role": "admin"}
CAROL = {**ADMIN, "X-Tenant-User-Id": "carol", "X-User-Role": "member"}  # team administrator
BOB = {**ADMIN, "X-Tenant-User-Id": "bob", "X-User-Role": "member"}  # team member
DAVE = {**ADMIN, "X-Tenant-User-Id": "dave", "X-User-Role": "member"}  # not in the team


async def _team_agent(org: str = ORG) -> tuple[str, AgentPersonaModel]:
    team = await create_team(None, org, "Platform", "", "root")
    await set_team_member(None, org, team.id, "bob", "member")
    agent = await save_persona(
        None,
        org,
        "root",
        True,
        AgentPersonaModel(
            id=f"{org}-agent",
            name="Team agent",
            role_title="Engineer",
            system_prompt="Work.",
            scope="team",
            team_id=team.id,
        ),
    )
    return team.id, agent


async def test_a_teams_default_permission_mode_applies_to_its_agents_only() -> None:
    team_id, agent = await _team_agent()
    await save_team_config(None, ORG, team_id, "user_preferences", {"permission_mode": "plan"})
    assert await permission_mode_for(None, ORG, "bob", agent, None) == "plan"
    assert await permission_mode_for(None, ORG, "bob", None, None) == "auto"
    await save_user_config(None, ORG, "bob", "user_preferences", {"permission_mode": "ask"})
    assert await permission_mode_for(None, ORG, "bob", agent, None) == "ask"


async def test_the_organizations_lock_holds_against_a_teams_default() -> None:
    team_id, agent = await _team_agent()
    await save_org_config(
        None, ORG, "user_preferences", {"permission_mode": "auto_edit"}, ["permission_mode"]
    )
    await save_team_config(None, ORG, team_id, "user_preferences", {"permission_mode": "plan"})
    assert await permission_mode_for(None, ORG, "bob", agent, None) == "auto_edit"


async def test_a_whole_turn_of_the_teams_agent_runs_under_its_default() -> None:
    org = "team_cfg_turn"
    team_id, agent = await _team_agent(org)
    await save_team_config(None, org, team_id, "user_preferences", {"permission_mode": "plan"})
    write = [("file_write", {"path": "a.txt", "content": "x"})]
    in_team = await run_turn(org, user="bob", metadata={"agent_id": agent.id}, script=write)
    assert "plan mode" in in_team.text_of_events()
    outside = await run_turn(org, user="bob", script=write)
    assert "plan mode" not in outside.text_of_events()


def _team(client: TestClient) -> str:
    team_id = str(client.post("/v1/teams", json={"name": "Platform"}, headers=ADMIN).json()["id"])
    client.put(f"/v1/teams/{team_id}/members/carol", json={"role": "admin"}, headers=ADMIN)
    client.put(f"/v1/teams/{team_id}/members/bob", json={"role": "member"}, headers=ADMIN)
    return team_id


def test_team_administrators_set_the_teams_defaults(client: TestClient) -> None:
    team_id = _team(client)
    path = f"/v1/teams/{team_id}/settings/user_preferences"
    body = {"config": {"permission_mode": "plan"}}
    assert client.put(path, json=body, headers=BOB).status_code == 403
    saved = client.put(path, json=body, headers=CAROL)
    assert saved.status_code == 200, saved.text
    view = saved.json()
    assert view["config"] == {"permission_mode": "plan"}
    assert view["provenance"]["permission_mode"]["source"] == f"team:{team_id}"
    assert "permission_mode" in view["writable"]
    assert client.get(path, headers=CAROL).json()["effective"]["permission_mode"] == "plan"


def test_a_team_cannot_set_what_only_the_organization_decides(client: TestClient) -> None:
    team_id = _team(client)
    client.put(
        "/v1/admin/settings/user_preferences",
        json={"config": {"permission_mode": "auto"}, "locked_keys": ["permission_mode"]},
        headers=ADMIN,
    )
    path = f"/v1/teams/{team_id}/settings/user_preferences"
    locked = client.put(path, json={"config": {"permission_mode": "plan"}}, headers=CAROL)
    assert locked.status_code == 422 and "locked by the organization" in locked.json()["detail"]
    assert "permission_mode" in client.get(path, headers=CAROL).json()["locked_by_org"]
    unknown = client.put(f"/v1/teams/{team_id}/settings/nope", json={"config": {}}, headers=CAROL)
    assert unknown.status_code == 404


SANDBOX: dict[str, Any] = {"name": "Platform box", "image": "ghcr.io/acme/platform:1"}


def test_a_teams_sandbox_type_is_listed_for_its_members_only(client: TestClient) -> None:
    team_id = _team(client)
    path = f"/v1/teams/{team_id}/sandboxes"
    secret = {**SANDBOX, "env": [{"name": "TOKEN", "value": "s3cr3t-value", "secret": True}]}
    assert client.post(path, json=secret, headers=BOB).status_code == 403
    created = client.post(path, json=secret, headers=CAROL)
    assert created.status_code == 200, created.text
    entry = created.json()
    assert entry["team_id"] == team_id and "s3cr3t" not in created.text

    def listed(headers: dict[str, str]) -> dict[str, Any]:
        found = client.get("/v1/sandboxes", headers=headers).json()["sandboxes"]
        return {s["id"]: s for s in found}

    assert entry["id"] in listed(BOB) and entry["id"] in listed(ADMIN)
    assert entry["id"] not in listed(DAVE)
    assert listed(BOB)[entry["id"]]["env"] == [], "only those who manage it see its variables"
    assert listed(CAROL)[entry["id"]]["env"][0]["name"] == "TOKEN"
    owned = client.get(f"/v1/teams/{team_id}/members", headers=CAROL).json()["owned"]
    assert owned == {"sandboxes": 1}
    assert client.delete(f"{path}/{entry['id']}", headers=BOB).status_code == 403
    assert client.delete(f"{path}/{entry['id']}", headers=CAROL).status_code == 200


def test_a_teams_sandbox_type_stays_inside_the_organizations_sizing(client: TestClient) -> None:
    team_id = _team(client)
    client.put(
        "/v1/admin/settings/sandboxes",
        json={"config": {"cpu_limit": "4", "memory_limit": "8Gi"}, "locked_keys": []},
        headers=ADMIN,
    )
    path = f"/v1/teams/{team_id}/sandboxes"
    big = client.post(path, json={**SANDBOX, "memory_limit": "16Gi"}, headers=CAROL)
    assert big.status_code == 422 and "memory_limit" in big.json()["detail"]
    placed = client.post(path, json={**SANDBOX, "profile": "gpu"}, headers=CAROL)
    assert placed.status_code == 403
    fits = client.post(
        path, json={**SANDBOX, "cpu_limit": "2", "memory_limit": "4Gi"}, headers=CAROL
    )
    assert fits.status_code == 200, fits.text
    default = client.post(
        path, json={**SANDBOX, "name": "Other", "is_default": True}, headers=CAROL
    )
    assert default.status_code == 422


def test_only_the_teams_agents_and_members_start_in_its_type(client: TestClient) -> None:
    team_id = _team(client)
    entry = client.post(f"/v1/teams/{team_id}/sandboxes", json=SANDBOX, headers=CAROL).json()
    agent = {"name": "A", "role_title": "R", "system_prompt": "p", "sandbox_id": entry["id"]}
    team_agent = client.post(
        "/v1/agents", json={**agent, "id": "ta", "scope": "team", "team_id": team_id}, headers=CAROL
    )
    assert team_agent.status_code == 200, team_agent.text
    org_agent = client.post("/v1/agents", json={**agent, "id": "oa", "scope": "org"}, headers=ADMIN)
    assert org_agent.status_code == 422
    session = {"title": "t", "sandbox_id": entry["id"]}
    assert client.post("/v1/sessions", json=session, headers=BOB).status_code in (200, 201)
    assert client.post("/v1/sessions", json=session, headers=DAVE).status_code == 422


async def test_a_turn_picks_a_teams_type_only_in_that_teams_context() -> None:
    catalogue = SandboxCatalogue(None)
    entry = await catalogue.create(ORG, name="Team box", image="img:1", team_id="platform")
    chosen = await catalogue.chosen(
        ORG,
        session_sandbox_id=entry.id,
        session_image=None,
        agent_sandbox_id="",
        teams=frozenset({"platform"}),
    )
    assert chosen is not None and chosen.id == entry.id
    elsewhere = await catalogue.chosen(
        ORG, session_sandbox_id=entry.id, session_image=None, agent_sandbox_id=""
    )
    assert elsewhere is None
    assert not await catalogue.image_is_offered(ORG, "img:1", [])
