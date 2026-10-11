"""Skills are tenant data: one organization must never see or delete another's."""

from __future__ import annotations

import pytest
from api.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        app.state.db_manager = None  # exercise the process-local store
        yield c


def _headers(org: str, user: str, role: str = "developer") -> dict[str, str]:
    return {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": user, "X-User-Role": role}


def test_skills_do_not_cross_organizations(client: TestClient) -> None:
    created = client.post(
        "/v1/skills",
        json={"name": "secret-playbook", "body": "internal steps", "scope": "org"},
        headers=_headers("test_org", "admin_user", "admin"),
    )
    assert created.status_code == 200
    skill_id = created.json()["id"]

    other = client.get("/v1/skills", headers=_headers("usage_org", "usage_user"))
    assert other.status_code == 200
    assert "secret-playbook" not in [s["name"] for s in other.json()]
    assert (
        client.delete(
            f"/v1/skills/{skill_id}", headers=_headers("usage_org", "usage_user", "admin")
        ).status_code
        == 404
    )

    own = client.get("/v1/skills", headers=_headers("test_org", "member_user"))
    assert "secret-playbook" in [s["name"] for s in own.json()]


def test_a_members_personal_skill_is_private_to_them(client: TestClient) -> None:
    client.post(
        "/v1/skills",
        json={"name": "my-notes", "body": "mine", "scope": "user"},
        headers=_headers("test_org", "user_1"),
    )
    assert "my-notes" in [
        s["name"] for s in client.get("/v1/skills", headers=_headers("test_org", "user_1")).json()
    ]
    assert "my-notes" not in [
        s["name"] for s in client.get("/v1/skills", headers=_headers("test_org", "user_2")).json()
    ]
