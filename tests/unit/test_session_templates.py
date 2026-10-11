"""Session templates / playbooks."""

from __future__ import annotations

import pytest
from api.main import app
from api.services.session_templates import TemplateStore
from fastapi.testclient import TestClient

MEMBER = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "tpl_member",
    "X-User-Role": "developer",
}
OTHER = {**MEMBER, "X-Tenant-User-Id": "tpl_other"}
ADMIN = {**MEMBER, "X-Tenant-User-Id": "tpl_admin", "X-User-Role": "admin"}
OTHER_ORG = {"X-Tenant-Org-Id": "usage_org", "X-Tenant-User-Id": "tpl_x", "X-User-Role": "admin"}
READONLY = {**MEMBER, "X-Tenant-User-Id": "tpl_ro", "X-User-Role": "readonly"}
BODY = {
    "name": "Fix flaky test",
    "prompt": "Find and fix the flaky test in the suite",
    "git_repo": "acme/widgets",
    "permission_mode": "ask",
}


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        app.state.db_manager = None
        app.state.template_store = TemplateStore(None)
        yield c


def _names(client: TestClient, headers: dict[str, str]) -> set[str]:
    return {t["name"] for t in client.get("/v1/templates", headers=headers).json()}


def test_personal_templates_are_private_and_org_templates_are_shared(client: TestClient) -> None:
    assert client.post("/v1/templates", json=BODY, headers=MEMBER).status_code == 200
    org = client.post(
        "/v1/templates", json={**BODY, "name": "Release checklist", "scope": "org"}, headers=ADMIN
    )
    assert org.status_code == 200 and org.json()["scope"] == "org"

    assert _names(client, MEMBER) == {"Fix flaky test", "Release checklist"}
    assert _names(client, OTHER) == {"Release checklist"}
    assert _names(client, OTHER_ORG) == set()


def test_only_administrators_manage_organization_templates(client: TestClient) -> None:
    assert (
        client.post("/v1/templates", json={**BODY, "scope": "org"}, headers=MEMBER).status_code
        == 403
    )
    org_id = client.post("/v1/templates", json={**BODY, "scope": "org"}, headers=ADMIN).json()["id"]
    assert client.delete(f"/v1/templates/{org_id}", headers=MEMBER).status_code == 403
    assert client.delete(f"/v1/templates/{org_id}", headers=ADMIN).status_code == 200


def test_a_member_cannot_see_or_delete_anothers_template(client: TestClient) -> None:
    mine = client.post("/v1/templates", json=BODY, headers=MEMBER).json()["id"]
    assert client.delete(f"/v1/templates/{mine}", headers=OTHER).status_code == 404
    assert client.post(f"/v1/templates/{mine}/start", headers=OTHER).status_code == 404
    assert client.delete(f"/v1/templates/{mine}", headers=MEMBER).status_code == 200


def test_read_only_members_cannot_create(client: TestClient) -> None:
    assert client.post("/v1/templates", json=BODY, headers=READONLY).status_code == 403


@pytest.mark.parametrize(
    "patch", [{"git_repo": "file:///etc"}, {"permission_mode": "yolo"}, {"git_branch": "--x"}]
)
def test_unsafe_templates_are_refused_when_saved(client: TestClient, patch: dict[str, str]) -> None:
    assert client.post("/v1/templates", json={**BODY, **patch}, headers=MEMBER).status_code == 422


def test_starting_a_template_creates_a_configured_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api.routers import session_crud as templates_router

    started_turns: list[str] = []
    monkeypatch.setattr(
        templates_router, "_spawn_background_turn", lambda **kw: started_turns.append(kw["prompt"])
    )
    template_id = client.post(
        "/v1/templates", json={**BODY, "agent_id": "persona-general"}, headers=MEMBER
    ).json()["id"]
    started = client.post(f"/v1/templates/{template_id}/start", headers=MEMBER)
    assert started.status_code == 200, started.text
    session = started.json()
    assert session["title"] == "Fix flaky test"
    assert session["git_repo"] is None, "sessions start blank; the repository is only a hint"
    assert session["agent_id"] == "persona-general"
    assert session["metadata"]["permission_mode"] == "ask"
    first_message = session["conversation_history"][0]
    assert first_message["content"].endswith("Find and fix the flaky test in the suite")
    assert first_message["author_user_id"] == "tpl_member"
    assert len(started_turns) == 1
    assert "acme/widgets" in started_turns[0] and "Find and fix the flaky test" in started_turns[0]
