"""Unit tests for the curated MCP connector catalog and org-wide authentication.

Validates that organizations can onboard productivity connectors (Notion,
Jira, Google Drive, ...) without a bespoke adapter per tool, choosing between a
shared service credential (org-wide, no user consent) and per-user OAuth.
"""

import pytest
from api.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def _headers(role: str = "developer", user: str = "dev_user") -> dict[str, str]:
    return {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": user,
        "X-User-Role": role,
    }


def test_list_mcp_catalog_exposes_auth_modes(client: TestClient) -> None:
    """The catalog lists curated connectors with their supported auth modes."""
    res = client.get("/v1/mcp/catalog", headers=_headers())
    assert res.status_code == 200
    entries = {e["id"]: e for e in res.json()}
    assert {"jira", "notion", "google-drive", "github", "slack", "linear"}.issubset(entries)

    jira = entries["jira"]
    assert jira["supports_org_wide_auth"] is True
    assert jira["supports_per_user_oauth"] is True
    assert "oauth2" in jira["supported_auth_types"]

    # Google Drive cannot be authenticated with a simple org-wide API key.
    assert entries["google-drive"]["supports_org_wide_auth"] is False


def test_regular_user_cannot_install_org_wide_connector(client: TestClient) -> None:
    """Non-admins cannot install an organization-wide (shared credential) connector."""
    res = client.post(
        "/v1/mcp/catalog/notion/install",
        json={
            "scope": "org",
            "auth_type": "api_key",
            "api_key": "ntn_super_secret_token",
            "endpoint_or_command": "https://mcp.notion.example.internal/mcp",
        },
        headers=_headers(role="developer"),
    )
    assert res.status_code == 403


def test_admin_installs_org_wide_service_credential(client: TestClient) -> None:
    """An admin installs a connector with an org-wide service API key (Slack-bot usable)."""
    secret = "ntn_super_secret_token"
    res = client.post(
        "/v1/mcp/catalog/notion/install",
        json={
            "scope": "org",
            "auth_type": "api_key",
            "api_key": secret,
            "endpoint_or_command": "https://mcp.notion.example.internal/mcp",
        },
        headers=_headers(role="admin", user="admin_user"),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["scope"] == "org"
    assert data["has_api_key"] is True
    assert data["api_key_fingerprint"]
    # Secrets must remain write-only and never be echoed back.
    assert secret not in res.text


def test_google_drive_rejects_org_wide_without_delegation(client: TestClient) -> None:
    """Google Drive requires per-user consent, so org-wide install is rejected."""
    res = client.post(
        "/v1/mcp/catalog/google-drive/install",
        json={
            "scope": "org",
            "auth_type": "oauth2",
            "client_id": "gdrive-client",
            "client_secret": "gdrive-secret",
            "endpoint_or_command": "https://drivemcp.example.internal/mcp",
        },
        headers=_headers(role="admin", user="admin_user"),
    )
    assert res.status_code == 400
    assert "per-user" in res.json()["detail"].lower()


def test_unknown_catalog_connector_returns_404(client: TestClient) -> None:
    res = client.post(
        "/v1/mcp/catalog/does-not-exist/install",
        json={"scope": "user", "auth_type": "oauth2"},
        headers=_headers(),
    )
    assert res.status_code == 404


def test_user_installs_per_user_oauth_connector(client: TestClient) -> None:
    """A regular user installs a connector scoped to their own OAuth identity."""
    res = client.post(
        "/v1/mcp/catalog/jira/install",
        json={
            "scope": "user",
            "auth_type": "oauth2",
            "client_id": "atlassian-client",
            "endpoint_or_command": "https://mcp.atlassian.example.internal/mcp",
        },
        headers=_headers(role="developer"),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["scope"] == "user"
    assert data["oauth_status"] == "pending"
