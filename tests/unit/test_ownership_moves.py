"""Moving objects between owners: what a move would break or narrow, and the moves of keys,
sandbox types and templates."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

ORG = "moves_org"
ADMIN = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "root", "X-User-Role": "admin"}
CAROL = {**ADMIN, "X-Tenant-User-Id": "carol", "X-User-Role": "member"}  # team administrator
BOB = {**ADMIN, "X-Tenant-User-Id": "bob", "X-User-Role": "member"}  # team member
DAVE = {**ADMIN, "X-Tenant-User-Id": "dave", "X-User-Role": "member"}  # not in the team
AGENT: dict[str, Any] = {"name": "A", "role_title": "R", "system_prompt": "Work."}


def _team(client: TestClient, name: str = "Platform") -> str:
    team_id = str(client.post("/v1/teams", json={"name": name}, headers=ADMIN).json()["id"])
    client.put(f"/v1/teams/{team_id}/members/carol", json={"role": "admin"}, headers=ADMIN)
    client.put(f"/v1/teams/{team_id}/members/bob", json={"role": "member"}, headers=ADMIN)
    return team_id


def _preview(client: TestClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    found = client.post("/v1/ownership/preview", json=body, headers=headers)
    assert found.status_code == 200, found.text
    return dict(found.json())


def _team_key_and_agent(client: TestClient, team_id: str) -> tuple[str, str]:
    key = client.post(
        f"/v1/teams/{team_id}/credentials",
        json={"provider": "anthropic", "api_key": "sk-team-move-0001"},
        headers=CAROL,
    ).json()["id"]
    agent = client.post(
        "/v1/agents",
        json={
            **AGENT,
            "id": "team-bound",
            "scope": "team",
            "team_id": team_id,
            "credential_bindings": [{"provider": "anthropic", "credential_id": key}],
        },
        headers=CAROL,
    )
    assert agent.status_code == 200, agent.text
    return key, str(agent.json()["id"])


def test_an_agent_bound_to_a_teams_key_cannot_leave_the_team_without_it(
    client: TestClient,
) -> None:
    team_id = _team(client)
    key, agent = _team_key_and_agent(client, team_id)
    preview = _preview(client, ADMIN, kind="agent", id=agent, scope="org")
    assert preview["audience"] == "Everyone in the organization"
    assert not preview["allowed"]
    assert [(p["kind"], p["id"], p["breaks"]) for p in preview["problems"]] == [
        ("credential", key, True)
    ]
    assert _preview(client, CAROL, kind="agent", id=agent, scope="org")["allowed"] is False
    same_team = _preview(client, CAROL, kind="agent", id=agent, scope="team", team_id=team_id)
    assert same_team["allowed"] and same_team["problems"] == []


def test_a_key_moves_only_where_its_agents_keep_it(client: TestClient) -> None:
    team_id = _team(client)
    other = _team(client, "Payments")
    key, _ = _team_key_and_agent(client, team_id)
    to_other = client.post(
        f"/v1/credentials/{key}/move", json={"scope": "team", "team_id": other}, headers=ADMIN
    )
    assert to_other.status_code == 409
    assert to_other.json()["detail"]["problems"][0]["kind"] == "agent"
    moved = client.post(f"/v1/credentials/{key}/move", json={"scope": "org"}, headers=ADMIN)
    assert moved.status_code == 200, moved.text
    assert (moved.json()["owner"], moved.json()["team_id"]) == ("org", None)
    assert (
        client.post(f"/v1/credentials/{key}/move", json={"scope": "org"}, headers=CAROL).status_code
        == 403
    )


def test_a_sandbox_type_used_outside_a_team_cannot_become_the_teams(client: TestClient) -> None:
    team_id = _team(client)
    box = client.post(
        "/v1/admin/sandboxes", json={"name": "Shared box", "image": "img:1"}, headers=ADMIN
    ).json()
    client.post(
        "/v1/agents",
        json={**AGENT, "id": "org-box", "scope": "org", "sandbox_id": box["id"]},
        headers=ADMIN,
    )
    refused = client.post(
        f"/v1/sandboxes/{box['id']}/move", json={"scope": "team", "team_id": team_id}, headers=ADMIN
    )
    assert refused.status_code == 409
    unused = client.post(
        "/v1/admin/sandboxes", json={"name": "Spare box", "image": "img:2"}, headers=ADMIN
    ).json()
    moved = client.post(
        f"/v1/sandboxes/{unused['id']}/move",
        json={"scope": "team", "team_id": team_id},
        headers=ADMIN,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["team_id"] == team_id
    listed = {s["id"] for s in client.get("/v1/sandboxes", headers=DAVE).json()["sandboxes"]}
    assert unused["id"] not in listed and box["id"] in listed
    assert (
        _preview(client, ADMIN, kind="sandbox", id=unused["id"], scope="user")["allowed"] is False
    )


def test_a_skill_moved_to_fewer_people_warns_about_the_agents_that_name_it(
    client: TestClient,
) -> None:
    team_id = _team(client)
    skill = client.post(
        "/v1/skills", json={"name": "deploy", "body": "Deploy.", "scope": "org"}, headers=ADMIN
    ).json()
    client.post(
        "/v1/agents",
        json={**AGENT, "id": "org-skilled", "scope": "org", "skill_ids": [skill["id"]]},
        headers=ADMIN,
    )
    preview = _preview(client, ADMIN, kind="skill", id=skill["id"], scope="team", team_id=team_id)
    assert preview["allowed"], "a narrowing is said, not refused"
    assert [(p["id"], p["breaks"]) for p in preview["problems"]] == [("org-skilled", False)]


def test_a_mission_cannot_move_where_its_runs_lose_its_agent(client: TestClient) -> None:
    mine = client.post(
        "/v1/agents", json={**AGENT, "id": "carols", "scope": "user"}, headers=CAROL
    ).json()
    mission = client.post(
        "/v1/missions",
        json={
            "name": "Mine",
            "instructions": "x",
            "agent_id": mine["id"],
            "triggers": [{"source": "api"}],
        },
        headers=CAROL,
    )
    assert mission.status_code == 200, mission.text
    preview = _preview(client, ADMIN, kind="mission", id=mission.json()["id"], scope="org")
    assert not preview["allowed"] and preview["problems"][0]["kind"] == "agent"


def test_a_teams_template_is_its_members_and_its_administrators_to_manage(
    client: TestClient,
) -> None:
    team_id = _team(client)
    body = {
        "name": "Release notes",
        "prompt": "Draft the notes.",
        "scope": "team",
        "team_id": team_id,
    }
    assert client.post("/v1/templates", json=body, headers=BOB).status_code == 403
    created = client.post("/v1/templates", json=body, headers=CAROL)
    assert created.status_code == 200, created.text
    template = created.json()
    assert (template["scope"], template["team_id"]) == ("team", team_id)
    assert template["id"] in {t["id"] for t in client.get("/v1/templates", headers=BOB).json()}
    assert template["id"] not in {t["id"] for t in client.get("/v1/templates", headers=DAVE).json()}
    assert client.delete(f"/v1/templates/{template['id']}", headers=BOB).status_code == 403
    assert client.delete(f"/v1/templates/{template['id']}", headers=CAROL).status_code == 200


def test_a_template_moves_to_the_team_its_owner_administers(client: TestClient) -> None:
    team_id = _team(client)
    body = {"name": "Notes", "prompt": "Draft the notes.", "scope": "user"}
    mine = client.post("/v1/templates", json=body, headers=CAROL).json()
    moved = client.put(
        f"/v1/templates/{mine['id']}",
        json={**body, "scope": "team", "team_id": team_id},
        headers=CAROL,
    )
    assert moved.status_code == 200, moved.text
    assert (moved.json()["scope"], moved.json()["team_id"]) == ("team", team_id)
    assert mine["id"] in {t["id"] for t in client.get("/v1/templates", headers=BOB).json()}
    to_org = client.put(f"/v1/templates/{mine['id']}", json={**body, "scope": "org"}, headers=CAROL)
    assert to_org.status_code == 403
    edit = client.put(
        f"/v1/templates/{mine['id']}",
        json={**body, "scope": "team", "team_id": team_id},
        headers=BOB,
    )
    assert edit.status_code == 403


def test_every_member_may_name_a_bot_for_a_chat_delivery(client: TestClient) -> None:
    listed = client.get("/v1/missions/chat-bots", headers=BOB)
    assert listed.status_code == 200 and isinstance(listed.json(), list)
