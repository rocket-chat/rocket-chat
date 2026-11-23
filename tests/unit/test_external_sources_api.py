"""External sources over HTTP: connect a repository, browse it, import skills, follow it.

The repository is an in-memory stand-in for GitHub (``tests/support/fake_source.py``); the
API, ownership checks and stores are the real ones.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from api.main import app
from fastapi.testclient import TestClient

from tests.support.fake_source import MARKETPLACE, FakeRepository, install, skill_md

BQ = "teams/data/bq-envs"
SIGNOZ = "teams/backend/signoz"


def _headers(org: str, user: str, role: str) -> dict[str, str]:
    return {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": user, "X-User-Role": role}


@pytest.fixture
def org() -> str:
    return f"src_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def repo() -> FakeRepository:
    repo = FakeRepository()
    repo.commit(
        {
            ".claude-plugin/marketplace.json": MARKETPLACE,
            f"{BQ}/SKILL.md": skill_md("bq-envs", "BigQuery environments", "Run scripts/bq-envs."),
            f"{BQ}/scripts/bq-envs": "#!/bin/sh\necho bq",
            f"{SIGNOZ}/SKILL.md": skill_md("signoz", "Query SigNoz"),
            "misc/loose/SKILL.md": skill_md("loose", "In no plugin"),
        },
        executable=(f"{BQ}/scripts/bq-envs",),
    )
    install(app, repo, linked=("alice",))
    return repo


def _connect(client: TestClient, org: str, **fields: Any) -> dict[str, Any]:
    body = {"repository": "https://github.com/acme/agent-skills", "ref": "main", **fields}
    response = client.post(
        "/v1/external-sources", json=body, headers=_headers(org, "root", "admin")
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _import(client: TestClient, org: str, source_id: str, paths: list[str], **fields: Any) -> Any:
    headers = fields.pop("headers", _headers(org, "root", "admin"))
    return client.post(
        f"/v1/external-sources/{source_id}/import",
        json={"paths": paths, "scope": "org", **fields},
        headers=headers,
    )


def _skills(client: TestClient, org: str, user: str = "root", role: str = "admin") -> list[Any]:
    return list(client.get("/v1/skills", headers=_headers(org, user, role)).json())


def test_connecting_reads_the_repository_and_its_plugins(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    source = _connect(client, org)

    assert (source["repository"], source["status"], source["last_commit"]) == (
        "acme/agent-skills",
        "ok",
        repo.head,
    )
    assert (source["has_manifest"], source["plugin_count"], source["skill_count"]) == (True, 2, 3)
    catalogue = client.get(
        f"/v1/external-sources/{source['id']}/catalogue", headers=_headers(org, "bob", "member")
    ).json()
    assert {p["name"]: p["entries"] for p in catalogue["plugins"]} == {
        "data": [BQ],
        "backend": [SIGNOZ],
    }
    loose = next(e for e in catalogue["entries"] if e["path"] == "misc/loose")
    assert loose["plugins"] == []
    assert catalogue["notes"] and "another repository" in catalogue["notes"][0]


def test_a_repository_that_cannot_be_read_is_connected_with_the_reason(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    repo.error = "the credential may not read it, HTTP 403."

    source = _connect(client, org)

    assert source["status"] == "error" and "HTTP 403" in source["last_error"]


def test_only_whoever_manages_an_owner_connects_a_source_for_it(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    member = _headers(org, "bob", "member")
    body = {"repository": "acme/agent-skills"}

    as_org = client.post("/v1/external-sources", json={**body, "scope": "org"}, headers=member)
    shared_member_source = client.post(
        "/v1/external-sources", json={**body, "scope": "org", "auth": "member"}, headers=member
    )
    personal = client.post("/v1/external-sources", json={**body, "scope": "user"}, headers=member)

    assert as_org.status_code == 403
    assert shared_member_source.status_code == 422
    assert personal.status_code == 200 and personal.json()["user_id"] == "bob"
    assert client.get("/v1/external-sources", headers=_headers(org, "carol", "member")).json() == []


def test_another_organization_sees_nothing(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    source = _connect(client, org)
    other = _headers(f"{org}_x", "root", "admin")

    assert client.get("/v1/external-sources", headers=other).json() == []
    assert (
        client.get(f"/v1/external-sources/{source['id']}/catalogue", headers=other).status_code
        == 404
    )
    assert _import(client, f"{org}_x", source["id"], [BQ], headers=other).status_code == 404


def test_importing_a_plugin_creates_skills_that_show_their_source(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    source = _connect(client, org)

    response = _import(client, org, source["id"], [BQ, SIGNOZ])

    assert response.status_code == 200, response.text
    imported = {s["name"]: s for s in response.json()["imported"]}
    assert set(imported) == {"bq-envs", "signoz"}
    bq = next(s for s in _skills(client, org, "bob", "member") if s["name"] == "bq-envs")
    assert bq["body"] == "Run scripts/bq-envs."
    assert bq["source"]["repository"] == "acme/agent-skills"
    assert (bq["source"]["path"], bq["source"]["plugin"], bq["source"]["status"]) == (
        BQ,
        "data",
        "synced",
    )
    assert bq["source"]["files"] == ["scripts/bq-envs"]
    assert bq["source"]["url"].endswith(f"/acme/agent-skills/tree/main/{BQ}")
    catalogue = client.get(
        f"/v1/external-sources/{source['id']}/catalogue", headers=_headers(org, "root", "admin")
    ).json()
    assert next(e for e in catalogue["entries"] if e["path"] == BQ)["imported"][0]["scope"] == "org"


def test_a_skill_with_the_same_name_is_a_conflict_until_merged(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    admin = _headers(org, "root", "admin")
    local = client.post(
        "/v1/skills",
        json={"name": "signoz", "body": "Old local text", "scope": "org"},
        headers=admin,
    ).json()
    source = _connect(client, org)

    refused = _import(client, org, source["id"], [BQ, SIGNOZ]).json()

    assert refused["imported"] == []
    assert refused["conflicts"] == [{"path": SIGNOZ, "name": "signoz", "skill_id": local["id"]}]
    assert {s["name"] for s in _skills(client, org)} == {"signoz"}

    merged = _import(client, org, source["id"], [BQ, SIGNOZ], merge=[SIGNOZ])

    assert merged.status_code == 200
    signoz = next(s for s in _skills(client, org) if s["name"] == "signoz")
    assert signoz["id"] == local["id"] and signoz["body"] == "Do the thing."
    assert signoz["source"]["path"] == SIGNOZ


def test_a_member_cannot_import_for_the_organization(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    source = _connect(client, org)

    response = _import(client, org, source["id"], [BQ], headers=_headers(org, "bob", "member"))

    assert response.status_code == 403


def test_skills_of_a_member_source_only_go_to_that_member(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    alice = _headers(org, "alice", "member")
    source = client.post(
        "/v1/external-sources",
        json={"repository": "acme/agent-skills", "scope": "user", "auth": "member"},
        headers=alice,
    ).json()
    assert source["status"] == "ok"

    to_org = _import(client, org, source["id"], [BQ], headers=_headers(org, "root", "admin"))
    personal = _import(client, org, source["id"], [BQ], scope="user", headers=alice)

    assert to_org.status_code == 404  # an administrator does not even see her personal source
    assert personal.status_code == 200
    skill = personal.json()["imported"][0]
    moved = client.post(
        "/v1/skills",
        json={**{k: skill[k] for k in ("id", "name", "description", "body")}, "scope": "org"},
        headers=_headers(org, "root", "admin"),
    )
    assert moved.status_code == 409


def test_a_synchronised_skill_is_not_edited_here(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    source = _connect(client, org)
    skill = _import(client, org, source["id"], [SIGNOZ]).json()["imported"][0]
    admin = _headers(org, "root", "admin")
    same = {k: skill[k] for k in ("id", "name", "description", "body")}

    edited = client.post("/v1/skills", json={**same, "body": "Changed"}, headers=admin)
    deactivated = client.post("/v1/skills", json={**same, "is_active": False}, headers=admin)

    assert edited.status_code == 409 and "pull request" in edited.json()["detail"]
    assert deactivated.status_code == 200 and deactivated.json()["is_active"] is False


def test_synchronising_follows_changes_and_flags_what_left(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    source = _connect(client, org)
    _import(client, org, source["id"], [BQ, SIGNOZ])
    repo.change(f"{SIGNOZ}/SKILL.md", skill_md("signoz", "Query SigNoz logs", "New steps."))
    repo.change(f"{BQ}/SKILL.md", None)
    repo.change(f"{BQ}/scripts/bq-envs", None)

    sync = client.post(
        f"/v1/external-sources/{source['id']}/sync", headers=_headers(org, "root", "admin")
    ).json()

    assert (sync["updated"], sync["missing"]) == (1, 1)
    skills = {s["name"]: s for s in _skills(client, org)}
    assert skills["signoz"]["body"] == "New steps."
    assert skills["signoz"]["description"] == "Query SigNoz logs"
    assert skills["bq-envs"]["is_active"] is False
    assert skills["bq-envs"]["source"]["status"] == "missing"
    assert "no longer in acme/agent-skills" in skills["bq-envs"]["source"]["problem"]


def test_a_skill_that_comes_back_is_reactivated(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    source = _connect(client, org)
    _import(client, org, source["id"], [SIGNOZ])
    sync_url = f"/v1/external-sources/{source['id']}/sync"
    admin = _headers(org, "root", "admin")
    text = repo.files[f"{SIGNOZ}/SKILL.md"][0].decode()
    repo.change(f"{SIGNOZ}/SKILL.md", None)
    client.post(sync_url, headers=admin)

    repo.change(f"{SIGNOZ}/SKILL.md", text)
    client.post(sync_url, headers=admin)

    signoz = next(s for s in _skills(client, org) if s["name"] == "signoz")
    assert signoz["is_active"] is True and signoz["source"]["status"] == "synced"


def test_a_rename_onto_a_taken_name_keeps_the_old_name_and_says_why(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    admin = _headers(org, "root", "admin")
    client.post("/v1/skills", json={"name": "logs", "body": "Mine", "scope": "org"}, headers=admin)
    source = _connect(client, org)
    _import(client, org, source["id"], [SIGNOZ])
    repo.change(f"{SIGNOZ}/SKILL.md", skill_md("logs", "Renamed"))

    client.post(f"/v1/external-sources/{source['id']}/sync", headers=admin)

    skills = {s["name"]: s for s in _skills(client, org)}
    assert skills["signoz"]["description"] == "Renamed"
    assert "another skill here has that name" in skills["signoz"]["source"]["problem"]
    assert skills["logs"]["body"] == "Mine"


def test_disconnecting_keeps_the_skills_and_lets_them_be_edited(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    admin = _headers(org, "root", "admin")
    source = _connect(client, org)
    skill = _import(client, org, source["id"], [SIGNOZ]).json()["imported"][0]

    assert client.delete(f"/v1/external-sources/{source['id']}", headers=admin).status_code == 200
    kept = next(s for s in _skills(client, org) if s["id"] == skill["id"])
    edited = client.post(
        "/v1/skills",
        json={**{k: skill[k] for k in ("id", "name", "description")}, "body": "Now mine"},
        headers=admin,
    )

    assert kept["source"] is None
    assert edited.status_code == 200 and edited.json()["body"] == "Now mine"


def test_deleting_a_skill_forgets_where_it_came_from(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    admin = _headers(org, "root", "admin")
    source = _connect(client, org)
    skill = _import(client, org, source["id"], [SIGNOZ]).json()["imported"][0]

    client.delete(f"/v1/skills/{skill['id']}", headers=admin)

    listed = client.get("/v1/external-sources", headers=admin).json()
    assert listed[0]["imported_count"] == 0


def test_an_invalid_or_unknown_selection_is_refused(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    repo.change("broken/SKILL.md", "no front matter")
    source = _connect(client, org)

    unknown = _import(client, org, source["id"], ["nowhere"])
    broken = _import(client, org, source["id"], ["broken"])

    assert unknown.status_code == 422 and "not a skill of this source" in unknown.json()["detail"]
    assert broken.status_code == 422 and "front matter" in broken.json()["detail"]


def test_changing_the_branch_reads_it_again(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    source = _connect(client, org)
    admin = _headers(org, "root", "admin")

    moved = client.put(
        f"/v1/external-sources/{source['id']}", json={"ref": "develop"}, headers=admin
    ).json()
    renamed = client.put(
        f"/v1/external-sources/{source['id']}",
        json={"name": "Team skills", "ref": "main"},
        headers=admin,
    ).json()

    assert moved["status"] == "error" and "develop" in moved["last_error"]
    assert (renamed["name"], renamed["status"]) == ("Team skills", "ok")
    member = client.put(
        f"/v1/external-sources/{source['id']}",
        json={"name": "x"},
        headers=_headers(org, "bob", "member"),
    )
    assert member.status_code == 403
