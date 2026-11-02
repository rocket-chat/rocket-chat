"""Teams: organization administrators create them, team administrators run them, and the objects
a team owns are used by its members only."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

ORG = "teams_org"
ADMIN = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "root", "X-User-Role": "admin"}
CAROL = {**ADMIN, "X-Tenant-User-Id": "carol", "X-User-Role": "member"}  # team administrator
BOB = {**ADMIN, "X-Tenant-User-Id": "bob", "X-User-Role": "member"}  # team member
DAVE = {**ADMIN, "X-Tenant-User-Id": "dave", "X-User-Role": "member"}  # not in the team
VERA = {**ADMIN, "X-Tenant-User-Id": "vera", "X-User-Role": "viewer"}
OTHER_ORG = {**ADMIN, "X-Tenant-Org-Id": "elsewhere"}


def _platform(client: TestClient) -> str:
    created = client.post("/v1/teams", json={"name": "Platform"}, headers=ADMIN)
    assert created.status_code == 201, created.text
    team_id = str(created.json()["id"])
    for user, role in (("carol", "admin"), ("bob", "member"), ("vera", "admin")):
        joined = client.put(
            f"/v1/teams/{team_id}/members/{user}", json={"role": role}, headers=ADMIN
        )
        assert joined.status_code == 200, joined.text
    return team_id


def _agent(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "platform-reviewer",
        "name": "Platform reviewer",
        "role_title": "Reviewer",
        "system_prompt": "Review.",
        **overrides,
    }


def test_only_organization_administrators_create_and_delete_teams(client: TestClient) -> None:
    assert client.post("/v1/teams", json={"name": "Platform"}, headers=CAROL).status_code == 403
    team_id = _platform(client)
    assert client.post("/v1/teams", json={"name": "platform"}, headers=ADMIN).status_code == 409
    assert client.delete(f"/v1/teams/{team_id}", headers=CAROL).status_code == 403
    assert client.delete(f"/v1/teams/{team_id}", headers=ADMIN).status_code == 204
    assert client.get("/v1/teams", headers=ADMIN).json()["teams"] == []


def test_every_member_lists_the_teams_with_their_own_role(client: TestClient) -> None:
    team_id = _platform(client)
    by_user = {
        name: client.get("/v1/teams", headers=headers).json()["teams"][0]
        for name, headers in (("carol", CAROL), ("bob", BOB), ("dave", DAVE), ("root", ADMIN))
    }
    assert by_user["carol"]["my_role"] == "admin" and by_user["carol"]["can_manage"]
    assert by_user["bob"]["my_role"] == "member" and not by_user["bob"]["can_manage"]
    assert by_user["dave"]["my_role"] is None and not by_user["dave"]["can_manage"]
    assert by_user["root"]["can_manage"]
    assert by_user["bob"]["id"] == team_id
    assert client.get("/v1/teams", headers=OTHER_ORG).json()["teams"] == []


def test_a_team_administrator_manages_members_and_a_member_does_not(client: TestClient) -> None:
    team_id = _platform(client)
    path = f"/v1/teams/{team_id}/members"
    assert client.put(f"{path}/dave", json={"role": "member"}, headers=BOB).status_code == 403
    assert client.put(f"{path}/dave", json={"role": "member"}, headers=CAROL).status_code == 200
    assert client.put(f"{path}/dave", json={"role": "owner"}, headers=CAROL).status_code == 422
    assert (
        client.patch(f"/v1/teams/{team_id}", json={"name": "Core"}, headers=CAROL).json()["name"]
        == "Core"
    )
    assert client.delete(f"{path}/dave", headers=BOB).status_code == 403
    assert client.delete(f"{path}/bob", headers=BOB).status_code == 204  # leaving
    members = {m["user_id"] for m in client.get(path, headers=CAROL).json()["members"]}
    assert members == {"carol", "dave", "vera"}


def test_only_members_and_administrators_see_who_is_in_a_team(client: TestClient) -> None:
    team_id = _platform(client)
    assert client.get(f"/v1/teams/{team_id}/members", headers=DAVE).status_code == 404
    assert client.get(f"/v1/teams/{team_id}/members", headers=BOB).status_code == 200
    assert client.get(f"/v1/teams/{team_id}/members", headers=ADMIN).status_code == 200


def test_a_viewer_administers_no_team_whatever_their_team_role(client: TestClient) -> None:
    team_id = _platform(client)
    assert not client.get("/v1/teams", headers=VERA).json()["teams"][0]["can_manage"]
    refused = client.put(f"/v1/teams/{team_id}/members/dave", json={"role": "member"}, headers=VERA)
    assert refused.status_code == 403


def test_a_team_agent_serves_its_members_only(client: TestClient) -> None:
    team_id = _platform(client)
    created = client.post("/v1/agents", json=_agent(scope="team", team_id=team_id), headers=CAROL)
    assert created.status_code == 200, created.text
    agent = created.json()
    assert (agent["scope"], agent["team_id"], agent["user_id"]) == ("team", team_id, None)

    def listed(headers: dict[str, str]) -> bool:
        return agent["id"] in {a["id"] for a in client.get("/v1/agents", headers=headers).json()}

    assert listed(BOB) and listed(CAROL) and listed(ADMIN)
    assert not listed(DAVE)
    assert not listed(OTHER_ORG)


def test_only_the_team_administrators_change_a_team_agent(client: TestClient) -> None:
    team_id = _platform(client)
    agent = client.post(
        "/v1/agents", json=_agent(scope="team", team_id=team_id), headers=CAROL
    ).json()
    edit = {**agent, "description": "changed"}
    assert client.post("/v1/agents", json=edit, headers=BOB).status_code == 403
    assert client.delete(f"/v1/agents/{agent['id']}", headers=BOB).status_code == 403
    assert client.post("/v1/agents", json=edit, headers=CAROL).status_code == 200
    # An organization administrator's edit keeps the team as owner.
    kept = client.post("/v1/agents", json={**edit, "description": "again"}, headers=ADMIN).json()
    assert (kept["scope"], kept["team_id"]) == ("team", team_id)


def test_a_member_cannot_create_for_a_team_they_do_not_administer(client: TestClient) -> None:
    team_id = _platform(client)
    refused = client.post("/v1/agents", json=_agent(scope="team", team_id=team_id), headers=BOB)
    assert refused.status_code == 403
    unknown = client.post(
        "/v1/agents", json=_agent(scope="team", team_id="team_nope"), headers=ADMIN
    )
    assert unknown.status_code == 422


def test_moving_an_agent_needs_both_owners(client: TestClient) -> None:
    team_id = _platform(client)
    mine = client.post("/v1/agents", json=_agent(scope="user"), headers=CAROL).json()
    assert mine["user_id"] == "carol"
    moved = client.post(
        "/v1/agents", json={**mine, "scope": "team", "team_id": team_id}, headers=CAROL
    )
    assert moved.status_code == 200, moved.text
    assert (moved.json()["scope"], moved.json()["user_id"]) == ("team", None)
    to_org = client.post("/v1/agents", json={**moved.json(), "scope": "org"}, headers=CAROL)
    assert to_org.status_code == 403
    assert (
        client.post("/v1/agents", json={**moved.json(), "scope": "org"}, headers=ADMIN).json()[
            "scope"
        ]
        == "org"
    )


def test_an_administrator_editing_a_personal_agent_never_takes_it_over(
    client: TestClient,
) -> None:
    mine = client.post("/v1/agents", json=_agent(scope="user"), headers=CAROL).json()
    edited = client.post("/v1/agents", json={**mine, "description": "x"}, headers=ADMIN)
    assert edited.status_code == 200
    assert edited.json()["user_id"] == "carol"


def test_a_team_that_owns_objects_cannot_be_deleted(client: TestClient) -> None:
    team_id = _platform(client)
    agent = client.post(
        "/v1/agents", json=_agent(scope="team", team_id=team_id), headers=CAROL
    ).json()
    refused = client.delete(f"/v1/teams/{team_id}", headers=ADMIN)
    assert refused.status_code == 409 and "1 agents" in refused.json()["detail"]
    owned = client.get(f"/v1/teams/{team_id}/members", headers=CAROL).json()["owned"]
    assert owned == {"agents": 1}
    assert client.delete(f"/v1/agents/{agent['id']}", headers=CAROL).status_code == 200
    assert client.delete(f"/v1/teams/{team_id}", headers=ADMIN).status_code == 204


def test_team_skills_and_knowledge_follow_the_same_rules(client: TestClient) -> None:
    team_id = _platform(client)
    skill = client.post(
        "/v1/skills",
        json={"name": "deploy", "body": "Deploy.", "scope": "team", "team_id": team_id},
        headers=CAROL,
    )
    assert skill.status_code == 200, skill.text
    assert skill.json()["team_id"] == team_id
    names = {s["name"] for s in client.get("/v1/skills", headers=BOB).json()}
    assert "deploy" in names
    assert "deploy" not in {s["name"] for s in client.get("/v1/skills", headers=DAVE).json()}
    edit = {"id": skill.json()["id"], "name": "deploy", "body": "Mine now.", "scope": "user"}
    assert client.post("/v1/skills", json=edit, headers=BOB).status_code == 403
    assert client.delete(f"/v1/skills/{skill.json()['id']}", headers=BOB).status_code == 403

    kb = client.post(
        "/v1/knowledge/bases",
        json={"name": "Runbooks", "scope": "team", "team_id": team_id},
        headers=CAROL,
    )
    assert kb.status_code == 200, kb.text
    kb_id = kb.json()["id"]
    assert kb_id in {k["id"] for k in client.get("/v1/knowledge/bases", headers=BOB).json()}
    assert kb_id not in {k["id"] for k in client.get("/v1/knowledge/bases", headers=DAVE).json()}
    document = {"title": "On call", "content": "Page the on-call engineer."}
    path = f"/v1/knowledge/bases/{kb_id}/documents"
    assert client.post(path, json=document, headers=DAVE).status_code == 404
    assert client.post(path, json=document, headers=BOB).status_code == 403
    assert client.post(path, json=document, headers=CAROL).status_code == 200
    query = {"query": "on call"}
    assert (
        client.post(f"/v1/knowledge/bases/{kb_id}/query", json=query, headers=BOB).status_code
        == 200
    )
    assert (
        client.post(f"/v1/knowledge/bases/{kb_id}/query", json=query, headers=DAVE).status_code
        == 404
    )
    assert client.get(f"/v1/teams/{team_id}/members", headers=CAROL).json()["owned"] == {
        "skills": 1,
        "knowledge_bases": 1,
    }


def test_a_team_mcp_server_is_listed_for_its_members_only(
    client: TestClient, monkeypatch: Any
) -> None:
    from api.routers import mcp_servers

    async def offline(*_: Any, **__: Any) -> tuple[list[dict[str, Any]], str]:
        return [], "connected"

    monkeypatch.setattr(mcp_servers, "_tools_and_status", offline)
    team_id = _platform(client)
    body = {
        "name": "Team tracker",
        "transport": "http",
        "endpoint_or_command": "https://mcp.example.com/mcp",
        "scope": "team",
        "team_id": team_id,
    }
    assert client.post("/v1/mcp/servers", json=body, headers=BOB).status_code == 403
    created = client.post("/v1/mcp/servers", json=body, headers=CAROL)
    assert created.status_code == 200, created.text
    server_id = created.json()["id"]
    assert created.json()["team_id"] == team_id
    assert server_id in {s["id"] for s in client.get("/v1/mcp/servers", headers=BOB).json()}
    assert server_id not in {s["id"] for s in client.get("/v1/mcp/servers", headers=DAVE).json()}
    assert client.delete(f"/v1/mcp/servers/{server_id}", headers=BOB).status_code == 403
    assert client.delete(f"/v1/mcp/servers/{server_id}", headers=CAROL).status_code == 200


def test_an_organization_agent_can_be_granted_to_a_team(client: TestClient) -> None:
    team_id = _platform(client)
    agent = client.post("/v1/agents", json=_agent(scope="org"), headers=ADMIN).json()
    granted = client.put(
        f"/v1/agents/{agent['id']}/access",
        json={"grants": [{"principal_kind": "team", "principal_id": team_id}]},
        headers=ADMIN,
    )
    assert granted.status_code == 200, granted.text
    assert agent["id"] in {a["id"] for a in client.get("/v1/agents", headers=BOB).json()}
    assert agent["id"] not in {a["id"] for a in client.get("/v1/agents", headers=DAVE).json()}
    unknown = client.put(
        f"/v1/agents/{agent['id']}/access",
        json={"grants": [{"principal_kind": "team", "principal_id": "team_nope"}]},
        headers=ADMIN,
    )
    assert unknown.status_code == 422


def test_a_knowledge_base_is_renamed_and_moved_by_whoever_manages_both_owners(
    client: TestClient,
) -> None:
    team_id = _platform(client)
    kb = client.post(
        "/v1/knowledge/bases", json={"name": "Notes", "scope": "user"}, headers=CAROL
    ).json()
    path = f"/v1/knowledge/bases/{kb['id']}"
    assert client.patch(path, json={"name": "Mine"}, headers=BOB).status_code == 404
    moved = client.patch(path, json={"scope": "team", "team_id": team_id}, headers=CAROL)
    assert moved.status_code == 200, moved.text
    assert (moved.json()["scope"], moved.json()["team_id"], moved.json()["user_id"]) == (
        "team",
        team_id,
        None,
    )
    assert client.patch(path, json={"name": "Runbooks"}, headers=BOB).status_code == 403
    assert client.patch(path, json={"scope": "org"}, headers=CAROL).status_code == 403
    renamed = client.patch(path, json={"name": "Runbooks"}, headers=CAROL).json()
    assert (renamed["name"], renamed["scope"]) == ("Runbooks", "team")
    assert client.get(f"/v1/teams/{team_id}/members", headers=CAROL).json()["owned"] == {
        "knowledge_bases": 1
    }
