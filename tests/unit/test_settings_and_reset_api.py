"""Unit tests for Cascading Settings API and Session Reset endpoint."""

import asyncio
import uuid

import pytest
from api.main import app
from api.routers import session_crud
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_get_settings_defaults(client: TestClient) -> None:
    """GET /v1/settings/{domain} returns the defaults and says they are the platform's."""
    res = client.get(
        "/v1/settings/github",
        headers={"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "user_1"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["domain"] == "github"
    assert data["effective"]["publish_as"] == "member"
    assert data["provenance"]["publish_as"]["source"] == "platform"
    assert "field_status" not in data


def test_update_user_settings_override(client: TestClient) -> None:
    """PUT /v1/settings/{domain} allows member overrides of member-scoped keys."""
    res = client.put(
        "/v1/settings/user_preferences",
        json={"overrides": {"git_branch_prefix": "fix/"}},
        headers={"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "user_1"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["effective"]["git_branch_prefix"] == "fix/"
    assert data["provenance"]["git_branch_prefix"]["source"] == "member"


def test_members_cannot_write_organization_managed_keys(client: TestClient) -> None:
    headers = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "user_1"}
    res = client.put(
        "/v1/settings/github",
        json={"overrides": {"bot_author_name": "Someone else"}},
        headers=headers,
    )
    assert res.status_code == 422
    assert res.json()["detail"] == [
        {
            "key": "bot_author_name",
            "message": "can only be set by an organization administrator",
        }
    ]


def test_unknown_keys_values_and_domains_are_rejected(client: TestClient) -> None:
    headers = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "user_1"}
    unknown_key = client.put(
        "/v1/settings/user_preferences", json={"overrides": {"nonsense": 1}}, headers=headers
    )
    assert unknown_key.status_code == 422 and unknown_key.json()["detail"][0]["key"] == "nonsense"

    bad_value = client.put(
        "/v1/settings/user_preferences",
        json={"overrides": {"suggest_next_questions": "maybe"}},
        headers=headers,
    )
    assert bad_value.status_code == 422

    assert client.get("/v1/settings/not_a_domain", headers=headers).status_code == 404
    assert (
        client.put("/v1/settings/not_a_domain", json={"overrides": {}}, headers=headers).status_code
        == 404
    )


def test_admin_settings_are_validated_and_scoped(client: TestClient) -> None:
    admin = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "admin_user",
        "X-User-Role": "admin",
    }
    wrong_enum = client.put(
        "/v1/admin/settings/sandboxes", json={"config": {"cpu_limit": "lots"}}, headers=admin
    )
    assert wrong_enum.status_code == 422
    # Only member-overridable keys can be locked.
    lock_org_only = client.put(
        "/v1/admin/settings/github",
        json={"config": {"default_branch": "main"}, "locked_keys": ["default_branch"]},
        headers=admin,
    )
    assert lock_org_only.status_code == 422
    ok = client.put(
        "/v1/admin/settings/sandboxes",
        json={"config": {"cpu_limit": "4", "default_image": "node:20-slim"}},
        headers=admin,
    )
    assert ok.status_code == 200 and ok.json()["config"]["cpu_limit"] == 4.0


def test_admin_lockout_and_enforcement(client: TestClient) -> None:
    """Admin locks a key via /v1/admin/settings/{domain}; the member override is ignored."""
    admin_headers = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "admin_user",
        "X-User-Role": "admin",
    }
    admin_payload = {
        "config": {"preferred_model": "openrouter/acme/locked-model"},
        "locked_keys": ["preferred_model"],
    }
    admin_res = client.put(
        "/v1/admin/settings/user_preferences", json=admin_payload, headers=admin_headers
    )
    assert admin_res.status_code == 200

    user_headers = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "user_2",
        "X-User-Role": "member",
    }
    user_res = client.put(
        "/v1/settings/user_preferences",
        json={
            "overrides": {"preferred_model": "openrouter/other/model", "git_branch_prefix": "dev/"}
        },
        headers=user_headers,
    )
    assert user_res.status_code == 200
    user_data = user_res.json()
    assert user_data["effective"]["preferred_model"] == "openrouter/acme/locked-model"
    assert user_data["provenance"]["preferred_model"]["locked_by"] == "org"
    assert user_data["effective"]["git_branch_prefix"] == "dev/"
    assert user_data["provenance"]["git_branch_prefix"]["source"] == "member"
    assert "Locked keys ignored" in user_data["warning"]


def test_reset_user_settings(client: TestClient) -> None:
    """DELETE /v1/settings/{domain} resets user overrides back to org defaults."""
    headers = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "user_3"}
    client.put(
        "/v1/settings/user_preferences",
        json={"overrides": {"git_branch_prefix": "x/"}},
        headers=headers,
    )
    res = client.delete("/v1/settings/user_preferences", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["effective"]["git_branch_prefix"] == "rocket/"
    assert data["provenance"]["git_branch_prefix"]["source"] == "platform"


def test_session_reset_clean_slate(client: TestClient) -> None:
    """POST /v1/sessions/reset deletes active sessions and seats user in a pristine session."""
    headers = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "user_reset"}

    # 1. Create two sessions
    s1 = client.post("/v1/sessions", json={"title": "Old Session 1"}, headers=headers).json()
    s2 = client.post("/v1/sessions", json={"title": "Old Session 2"}, headers=headers).json()

    # Verify sessions exist
    list_res = client.get("/v1/sessions", headers=headers).json()
    ids = [s["session_id"] for s in list_res]
    assert s1["session_id"] in ids
    assert s2["session_id"] in ids

    # 2. Reset sessions
    reset_res = client.post("/v1/sessions/reset", headers=headers)
    assert reset_res.status_code == 200
    reset_data = reset_res.json()
    assert reset_data["status"] == "ok"
    assert reset_data["deleted_count"] >= 2
    new_session = reset_data["new_session"]
    assert new_session["title"] == "New Mission"
    assert new_session["session_id"] not in (s1["session_id"], s2["session_id"])
    assert new_session["checklist"] == []

    # 3. Verify only the new session exists for this user
    after_list = client.get("/v1/sessions", headers=headers).json()
    after_ids = [s["session_id"] for s in after_list]
    assert s1["session_id"] not in after_ids
    assert s2["session_id"] not in after_ids
    assert new_session["session_id"] in after_ids


def test_session_reset_deletes_only_the_callers_own_sessions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An administrator's reset keeps other members' sessions and pages past one batch."""
    monkeypatch.setattr(session_crud, "RESET_BATCH", 2)
    # Its own organization: a database kept between runs still holds the last run's sessions.
    org = f"reset_org_{uuid.uuid4().hex[:8]}"
    admin = {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": "admin", "X-User-Role": "admin"}
    member = {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": "member"}
    own = [
        client.post("/v1/sessions", json={}, headers=admin).json()["session_id"] for _ in range(5)
    ]
    theirs = client.post("/v1/sessions", json={"title": "Theirs"}, headers=member).json()
    client.post(
        f"/v1/sessions/{theirs['session_id']}/share", json={"is_shared": True}, headers=member
    )

    reset = client.post("/v1/sessions/reset", headers=admin).json()

    assert reset["deleted_count"] == 5
    everything = client.get("/v1/sessions?scope=organization", headers=admin).json()
    remaining = {s["session_id"] for s in everything}
    assert remaining == {theirs["session_id"], reset["new_session"]["session_id"]}
    assert not remaining & set(own)


def test_usage_telemetry_endpoints(client: TestClient) -> None:
    """Verify GET /v1/settings/org/usage and /v1/settings/user/usage reflect actual gateway data."""
    headers = {"X-Tenant-Org-Id": "usage_org", "X-Tenant-User-Id": "usage_user"}

    # Initially zero
    org_res = client.get("/v1/settings/org/usage", headers=headers)
    assert org_res.status_code == 200
    org_data = org_res.json()
    assert org_data["tenant_org_id"] == "usage_org"
    assert org_data["total_tokens"] >= 0
    assert org_data["total_cost_usd"] >= 0.0

    user_res = client.get("/v1/settings/user/usage", headers=headers)
    assert user_res.status_code == 200
    user_data = user_res.json()
    assert user_data["tenant_user_id"] == "usage_user"
    assert user_data["total_tokens"] >= 0
    assert user_data["total_cost_usd"] >= 0.0


def test_member_only_keys_are_not_organization_defaults(client: TestClient) -> None:
    from api.services.settings import effective_settings, save_org_config

    org = "scope_org"
    admin = {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": "scope_admin", "X-User-Role": "admin"}
    # A value saved while the key could still be an organization default.
    asyncio.run(save_org_config(None, org, "user_preferences", {"full_name": "Old Default"}, []))

    shown = client.get("/v1/admin/settings/user_preferences", headers=admin).json()
    effective = asyncio.run(effective_settings(None, org, "user_preferences"))

    assert "full_name" not in shown["config"] and "full_name" not in shown["system_defaults"]
    assert "git_branch_prefix" in shown["config"]
    assert effective["full_name"] == "", "a stale organization value no longer applies"
    saved = client.put(
        "/v1/admin/settings/user_preferences",
        json={"config": shown["config"], "locked_keys": []},
        headers=admin,
    )
    assert saved.status_code == 200
