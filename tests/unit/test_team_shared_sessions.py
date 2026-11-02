"""A session shared with a team opens to the team's members, for as long as they are in it."""

from __future__ import annotations

from fastapi.testclient import TestClient

ORG = "team_share"
ADMIN = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "root", "X-User-Role": "admin"}
CAROL = {**ADMIN, "X-Tenant-User-Id": "carol", "X-User-Role": "member"}  # the session's owner
BOB = {**ADMIN, "X-Tenant-User-Id": "bob", "X-User-Role": "member"}  # in the team
DAVE = {**ADMIN, "X-Tenant-User-Id": "dave", "X-User-Role": "member"}  # not in the team


def _shared_session(client: TestClient) -> tuple[str, str]:
    team_id = str(client.post("/v1/teams", json={"name": "Platform"}, headers=ADMIN).json()["id"])
    client.put(f"/v1/teams/{team_id}/members/bob", json={"role": "member"}, headers=ADMIN)
    created = client.post("/v1/sessions", json={"title": "Plan"}, headers=CAROL)
    assert created.status_code in (200, 201), created.text
    session_id = str(created.json()["session_id"])
    shared = client.put(
        f"/v1/sessions/{session_id}/sharing", json={"team_ids": [team_id]}, headers=CAROL
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["teams"] == [{"id": team_id, "name": "Platform"}]
    return team_id, session_id


def _listed(client: TestClient, headers: dict[str, str]) -> set[str]:
    return {s["session_id"] for s in client.get("/v1/sessions", headers=headers).json()}


def test_a_team_member_opens_and_lists_a_session_shared_with_the_team(
    client: TestClient,
) -> None:
    _, session_id = _shared_session(client)
    assert client.get(f"/v1/sessions/{session_id}", headers=BOB).status_code == 200
    assert session_id in _listed(client, BOB)
    assert client.get(f"/v1/sessions/{session_id}", headers=DAVE).status_code == 403
    assert session_id not in _listed(client, DAVE)


def test_leaving_the_team_closes_the_session_again(client: TestClient) -> None:
    team_id, session_id = _shared_session(client)
    assert client.delete(f"/v1/teams/{team_id}/members/bob", headers=BOB).status_code == 204
    assert client.get(f"/v1/sessions/{session_id}", headers=BOB).status_code == 403
    assert session_id not in _listed(client, BOB)


def test_only_teams_of_the_organization_are_shared_with(client: TestClient) -> None:
    created = client.post("/v1/sessions", json={"title": "Plan"}, headers=CAROL).json()
    refused = client.put(
        f"/v1/sessions/{created['session_id']}/sharing",
        json={"team_ids": ["team_elsewhere"]},
        headers=CAROL,
    )
    assert refused.status_code == 422
