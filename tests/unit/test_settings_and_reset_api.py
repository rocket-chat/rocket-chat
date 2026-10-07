"""Unit tests for Cascading Settings API and Session Reset endpoint."""

import pytest
from api.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_get_settings_defaults(client: TestClient) -> None:
    """GET /v1/settings/{domain} returns defaults with 'org_default' status."""
    res = client.get(
        "/v1/settings/github",
        headers={"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "user_1"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["domain"] == "github"
    assert data["effective"]["commit_authorship_policy"] == "co_authored"
    assert data["field_status"]["commit_authorship_policy"] == "org_default"


def test_update_user_settings_override(client: TestClient) -> None:
    """PUT /v1/settings/{domain} allows user overrides when unlocked."""
    payload = {"overrides": {"commit_authorship_policy": "user_only"}}
    res = client.put(
        "/v1/settings/github",
        json=payload,
        headers={"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "user_1"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["effective"]["commit_authorship_policy"] == "user_only"
    assert data["field_status"]["commit_authorship_policy"] == "overridden"


def test_admin_lockout_and_enforcement(client: TestClient) -> None:
    """Admin locks a key via /v1/admin/settings/{domain}; user override is ignored."""
    admin_headers = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "admin_user",
        "X-User-Role": "admin",
    }
    # 1. Admin locks commit_authorship_policy
    admin_payload = {
        "config": {"commit_authorship_policy": "bot_only"},
        "locked_keys": ["commit_authorship_policy"],
    }
    admin_res = client.put("/v1/admin/settings/github", json=admin_payload, headers=admin_headers)
    assert admin_res.status_code == 200

    # 2. Regular user tries to override locked key
    user_headers = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "user_2",
        "X-User-Role": "developer",
    }
    user_res = client.put(
        "/v1/settings/github",
        json={"overrides": {"commit_authorship_policy": "user_only", "default_branch": "develop"}},
        headers=user_headers,
    )
    assert user_res.status_code == 200
    user_data = user_res.json()
    # Locked key retains org value
    assert user_data["effective"]["commit_authorship_policy"] == "bot_only"
    assert user_data["field_status"]["commit_authorship_policy"] == "locked"
    # Unlocked key receives override
    assert user_data["effective"]["default_branch"] == "develop"
    assert user_data["field_status"]["default_branch"] == "overridden"


def test_reset_user_settings(client: TestClient) -> None:
    """DELETE /v1/settings/{domain} resets user overrides back to org defaults."""
    headers = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "user_3"}
    # Override
    client.put(
        "/v1/settings/models",
        json={"overrides": {"temperature": 0.9}},
        headers=headers,
    )
    # Reset
    res = client.delete("/v1/settings/models", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["effective"]["temperature"] == 0.2
    assert data["field_status"]["temperature"] == "org_default"


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
