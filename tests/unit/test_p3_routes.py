"""Credentials, spend limits, agent access and the caller's access over HTTP (P2.3, P3)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from api.main import app
from api.services.settings import reset_memory_settings
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    reset_memory_settings()
    with TestClient(app) as c:
        yield c


def _as(role: str, org: str, user: str = "u1") -> dict[str, str]:
    return {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": user, "X-User-Role": role}


def _org() -> str:
    return f"p3_{uuid.uuid4().hex[:8]}"


def test_my_access_lists_role_and_capabilities(client: TestClient) -> None:
    org = _org()
    member = client.get("/v1/me/access", headers=_as("developer", org)).json()
    assert member["role"] == "member"
    assert "bring_own_keys" in member["capabilities"]
    assert "manage_spend_limits" not in member["capabilities"]
    viewer = client.get("/v1/me/access", headers=_as("readonly", org)).json()
    assert (viewer["role"], viewer["capabilities"]) == ("viewer", [])


def test_organization_credentials_are_admin_only_and_never_return_the_secret(
    client: TestClient,
) -> None:
    org = _org()
    body = {
        "provider": "anthropic",
        "api_key": "sk-ant-secret-0001",
        "label": "Claude Pro",
        "restricted_to_agents": True,
    }
    assert (
        client.post("/v1/admin/credentials", json=body, headers=_as("member", org)).status_code
        == 403
    )
    created = client.post("/v1/admin/credentials", json=body, headers=_as("admin", org))
    assert created.status_code == 200, created.text
    view = created.json()
    assert view["restricted_to_agents"] and not view["default_for_provider"]
    assert "sk-ant-secret-0001" not in created.text
    listed = client.get("/v1/admin/credentials", headers=_as("admin", org)).json()
    assert [c["label"] for c in listed["credentials"]] == ["Claude Pro"]
    rotated = client.post(
        f"/v1/admin/credentials/{view['id']}/rotate",
        json={"api_key": "sk-ant-secret-0002"},
        headers=_as("admin", org),
    )
    assert rotated.status_code == 200 and rotated.json()["id"] == view["id"]
    assert client.delete(
        f"/v1/admin/credentials/{view['id']}", headers=_as("admin", org)
    ).json() == {"deleted": True}


@pytest.mark.setting("access.create_personal_agents")
def test_member_credentials_need_the_capability(client: TestClient) -> None:
    org = _org()
    body = {"provider": "openai", "api_key": "sk-member-00001", "label": "Mine"}
    created = client.post("/v1/user/credentials", json=body, headers=_as("member", org))
    assert created.status_code == 200, created.text
    assert (
        client.post("/v1/user/credentials", json=body, headers=_as("viewer", org)).status_code
        == 403
    )
    client.put(
        "/v1/admin/settings/access",
        json={
            "config": {"bring_own_keys": ["admin"], "create_personal_agents": []},
            "locked_keys": [],
        },
        headers=_as("admin", org),
    )
    refused = client.post("/v1/user/credentials", json=body, headers=_as("member", org))
    assert refused.status_code == 403 and "bring_own_keys" in refused.json()["detail"]
    personal = {
        "id": "p-mine",
        "name": "Mine",
        "role_title": "x",
        "system_prompt": "x",
        "scope": "user",
    }
    denied = client.post("/v1/agents", json=personal, headers=_as("member", org))
    assert denied.status_code == 403 and "create_personal_agents" in denied.json()["detail"]


@pytest.mark.setting("access.manage_spend_limits")
def test_spend_limits_need_manage_spend_limits(client: TestClient) -> None:
    org = _org()
    limit = {"subject_kind": "org", "period": "day", "amount_usd": 5, "mode": "block"}
    assert (
        client.post("/v1/admin/spend-limits", json=limit, headers=_as("member", org)).status_code
        == 403
    )
    created = client.post("/v1/admin/spend-limits", json=limit, headers=_as("admin", org))
    assert created.status_code == 200, created.text
    rows = client.get("/v1/admin/spend-limits", headers=_as("admin", org)).json()
    assert [(r["subject_kind"], r["period"], r["spent_usd"]) for r in rows] == [("org", "day", 0.0)]
    client.put(
        "/v1/admin/settings/access",
        json={"config": {"manage_spend_limits": []}, "locked_keys": []},
        headers=_as("admin", org),
    )
    assert (
        client.delete(
            f"/v1/admin/spend-limits/{created.json()['id']}", headers=_as("admin", org)
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/v1/admin/spend-limits/{created.json()['id']}", headers=_as("owner", org)
        ).status_code
        == 200
    )


def test_a_limit_names_its_subject(client: TestClient) -> None:
    org = _org()
    bad = {"subject_kind": "agent", "period": "day", "amount_usd": 5}
    assert (
        client.post("/v1/admin/spend-limits", json=bad, headers=_as("admin", org)).status_code
        == 422
    )
    ceiling = {"subject_kind": "org", "period": "day", "amount_usd": 5, "is_ceiling": True}
    assert (
        client.post("/v1/admin/spend-limits", json=ceiling, headers=_as("admin", org)).status_code
        == 422
    )


def test_agent_access_grants(client: TestClient) -> None:
    org = _org()
    agent = {
        "id": f"agent-{org}",
        "name": "Pro",
        "role_title": "x",
        "system_prompt": "x",
        "scope": "org",
    }
    saved = client.post("/v1/agents", json=agent, headers=_as("admin", org)).json()
    put = client.put(
        f"/v1/agents/{saved['id']}/access",
        json={"grants": [{"principal_kind": "member", "principal_id": "bob"}]},
        headers=_as("admin", org),
    )
    assert put.status_code == 200, put.text
    listed = {a["id"] for a in client.get("/v1/agents", headers=_as("member", org, "alice")).json()}
    assert saved["id"] not in listed
    listed = {a["id"] for a in client.get("/v1/agents", headers=_as("member", org, "bob")).json()}
    assert saved["id"] in listed
    assert (
        client.put(
            f"/v1/agents/{saved['id']}/access", json={"grants": []}, headers=_as("member", org)
        ).status_code
        == 403
    )
    session = client.post(
        "/v1/sessions",
        json={"title": "t", "agent_id": saved["id"]},
        headers=_as("member", org, "alice"),
    )
    assert session.status_code == 403


def test_bindings_are_validated_on_save(client: TestClient) -> None:
    org = _org()
    key = client.post(
        "/v1/admin/credentials",
        json={"provider": "anthropic", "api_key": "sk-ant-secret-0001", "label": "Pro"},
        headers=_as("admin", org),
    ).json()
    personal = {
        "id": "p-mine",
        "name": "Mine",
        "role_title": "x",
        "system_prompt": "x",
        "scope": "user",
        "credential_bindings": [{"provider": "anthropic", "credential_id": key["id"]}],
    }
    refused = client.post("/v1/agents", json=personal, headers=_as("member", org))
    assert refused.status_code == 403, (
        "a member cannot bind an organization key to a personal agent"
    )
    org_agent = {**personal, "id": "o-agent", "scope": "org"}
    bound = client.post("/v1/agents", json=org_agent, headers=_as("admin", org))
    assert bound.status_code == 200, bound.text
    assert bound.json()["credential_bindings"] == [
        {"provider": "anthropic", "credential_id": key["id"]}
    ]


def test_switching_to_an_organization_one_does_not_belong_to_is_refused(client: TestClient) -> None:
    # A person of their own: with a database, memberships persist across tests.
    org, person = _org(), f"u_{uuid.uuid4().hex[:8]}"
    res = client.post(
        "/v1/auth/switch-org", json={"org_id": "other"}, headers=_as("member", org, person)
    )
    # Refused: not a member there (403), or no database to switch with (400/503).
    assert res.status_code in (400, 403, 503)
    mine = client.get("/v1/me/organizations", headers=_as("member", org, person)).json()
    assert [o["id"] for o in mine["organizations"]] == [org]
