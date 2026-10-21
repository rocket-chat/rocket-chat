"""Unit tests for Scoped MCP servers, OAuth 2.1 PKCE, prompt skills, and multi-tenant knowledge bases."""

import pytest
from api.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_stdio_mcp_forbidden_for_regular_user(client: TestClient) -> None:
    """Regular users cannot register or validate stdio MCP servers (host container protection)."""
    user_headers = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "dev_user",
        "X-User-Role": "developer",
    }

    # 1. Validation attempt
    val_resp = client.post(
        "/v1/mcp/servers/validate",
        json={
            "transport": "stdio",
            "endpoint_or_command": "npx -y @modelcontextprotocol/server-github",
        },
        headers=user_headers,
    )
    assert val_resp.status_code == 200
    val_data = val_resp.json()
    assert val_data["valid"] is False
    assert "Only Organization Administrators" in val_data["error"]

    # 2. Registration attempt
    reg_resp = client.post(
        "/v1/mcp/servers",
        json={
            "name": "Host Terminal",
            "transport": "stdio",
            "endpoint_or_command": "sh",
            "scope": "user",
        },
        headers=user_headers,
    )
    assert reg_resp.status_code == 403


def test_admin_can_validate_and_register_stdio_mcp(client: TestClient) -> None:
    """Org Admins are authorized to register stdio MCP servers at org scope."""
    admin_headers = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "admin_user",
        "X-User-Role": "admin",
    }

    reg_resp = client.post(
        "/v1/mcp/servers",
        json={
            "name": "Admin Test Stdio",
            "transport": "stdio",
            "endpoint_or_command": "node -e 'process.exit(0)'",
            "scope": "org",
        },
        headers=admin_headers,
    )
    assert reg_resp.status_code == 200
    data = reg_resp.json()
    assert data["name"] == "Admin Test Stdio"
    assert data["scope"] == "org"


def test_user_can_register_personal_sse_mcp(client: TestClient) -> None:
    """Regular users can register personal SSE/HTTP MCP servers with their own auth."""
    user_headers = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "dev_alice",
        "X-User-Role": "developer",
    }

    reg_resp = client.post(
        "/v1/mcp/servers",
        json={
            "name": "Alice Private Tool",
            "transport": "sse",
            "endpoint_or_command": "https://example.com/sse",
            "scope": "user",
            "auth": {
                "auth_type": "api_key",
                "api_key": "secret-alice-key",
            },
        },
        headers=user_headers,
    )
    assert reg_resp.status_code == 200
    data = reg_resp.json()
    assert data["name"] == "Alice Private Tool"
    assert data["scope"] == "user"
    assert data["has_api_key"] is True

    # Check listing: Alice sees her tool
    list_resp = client.get("/v1/mcp/servers", headers=user_headers)
    assert list_resp.status_code == 200
    ids = [s["id"] for s in list_resp.json()]
    assert data["id"] in ids

    # Another user in same org cannot see Alice's personal tool
    bob_headers = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "dev_bob",
        "X-User-Role": "developer",
    }
    bob_list = client.get("/v1/mcp/servers", headers=bob_headers).json()
    bob_ids = [s["id"] for s in bob_list]
    assert data["id"] not in bob_ids


def test_skills_crud_with_org_and_user_scoping(client: TestClient) -> None:
    """Test simplified prompt skills creation, permission enforcement, and listing."""
    user_headers = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "dev_charlie",
        "X-User-Role": "developer",
    }

    # 1. Non-admin cannot create org-scoped skill
    err_resp = client.post(
        "/v1/skills",
        json={
            "name": "Global Formatting",
            "description": "Format all markdown",
            "body": "Always use 2 spaces for indentation.",
            "scope": "org",
        },
        headers=user_headers,
    )
    assert err_resp.status_code == 403

    # 2. Charlie creates user-scoped skill
    ok_resp = client.post(
        "/v1/skills",
        json={
            "name": "Charlie Helper",
            "description": "My custom prompt",
            "body": "Always reply concisely in bullet points.",
            "scope": "user",
        },
        headers=user_headers,
    )
    assert ok_resp.status_code == 200
    skill_data = ok_resp.json()
    assert skill_data["title"] == "Charlie Helper"
    assert skill_data["body"] == "Always reply concisely in bullet points."
    assert skill_data["scope"] == "user"

    # 3. Test skill endpoint returns success
    test_resp = client.post(
        f"/v1/skills/{skill_data['id']}/test",
        json={"test_arguments": {}},
        headers=user_headers,
    )
    assert test_resp.status_code == 200
    assert test_resp.json()["success"] is True


def test_knowledge_base_scoping(client: TestClient) -> None:
    """Test multi-tenant Knowledge Base scoping: org vs user."""
    user_headers = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "dev_david",
        "X-User-Role": "developer",
    }

    # 1. Non-admin cannot create org-scoped KB
    res_err = client.post(
        "/v1/knowledge/bases",
        json={"name": "Org Docs", "description": "Global documentation", "scope": "org"},
        headers=user_headers,
    )
    assert res_err.status_code == 403

    # 2. David creates user-scoped KB
    res_ok = client.post(
        "/v1/knowledge/bases",
        json={"name": "David Notes", "description": "Personal research", "scope": "user"},
        headers=user_headers,
    )
    assert res_ok.status_code == 200
    kb_data = res_ok.json()
    assert kb_data["name"] == "David Notes"
    assert kb_data["scope"] == "user"


def test_custom_agents_scoping_and_skill_binding(client: TestClient) -> None:
    """Test custom agents with scope enforcement and skill binding."""
    user_headers = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "dev_eve",
        "X-User-Role": "developer",
    }

    # Eve creates personal agent with attached skills
    agent_resp = client.post(
        "/v1/agents",
        json={
            "id": "agent-eve-tester",
            "name": "Eve Custom Tester",
            "role_title": "QA Specialist",
            "description": "Specialized testing agent",
            "system_prompt": "You are a test automation engineer.",
            "model": "openrouter/anthropic/claude-3.5-sonnet",
            "whitelisted_tools": ["file_read", "bash_exec"],
            "skill_ids": ["skill-charlie-helper"],
            "scope": "user",
        },
        headers=user_headers,
    )
    assert agent_resp.status_code == 200
    data = agent_resp.json()
    assert data["name"] == "Eve Custom Tester"
    assert data["scope"] == "user"
    assert "skill-charlie-helper" in data["skill_ids"]


def _stdio_server(auth: dict[str, object]) -> dict[str, object]:
    return {
        "name": "Env Checked",
        "transport": "stdio",
        "endpoint_or_command": "npx -y some-server",
        "scope": "org",
        "auth": auth,
    }


_ADMIN = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "admin", "X-User-Role": "admin"}


def test_a_command_server_takes_environment_variables_with_valid_names(
    client: TestClient,
) -> None:
    bad_name = client.post(
        "/v1/mcp/servers",
        json=_stdio_server(
            {"auth_type": "custom_headers", "headers": [{"name": "X-Org-ID", "value": "1"}]}
        ),
        headers=_ADMIN,
    )
    assert bad_name.status_code == 422 and "X-Org-ID" in bad_name.json()["detail"]

    request_auth = client.post(
        "/v1/mcp/servers",
        json=_stdio_server({"auth_type": "api_key", "api_key": "k"}),
        headers=_ADMIN,
    )
    assert request_auth.status_code == 422

    ok = client.post(
        "/v1/mcp/servers",
        json=_stdio_server(
            {
                "auth_type": "custom_headers",
                "headers": [{"name": "BRAVE_API_KEY", "value": "k", "is_secret": True}],
            }
        ),
        headers=_ADMIN,
    )
    assert ok.status_code in (200, 201), ok.text
    client.delete(f"/v1/mcp/servers/{ok.json()['id']}", headers=_ADMIN)


def test_usage_guidance_is_kept_with_the_server_and_has_a_size_limit(client: TestClient) -> None:
    admin = {
        "X-Tenant-Org-Id": "test_org",
        "X-Tenant-User-Id": "admin_user",
        "X-User-Role": "admin",
    }
    server = {
        "name": "Guided Stdio",
        "transport": "stdio",
        "endpoint_or_command": "node -e 'process.exit(0)'",
        "scope": "org",
    }
    guidance = "# Use for billing\n\nAlways confirm the currency first."

    saved = client.post("/v1/mcp/servers", json={**server, "guidance": guidance}, headers=admin)
    too_long = client.post(
        "/v1/mcp/servers", json={**server, "guidance": "x" * 8001}, headers=admin
    )

    assert saved.status_code == 200 and saved.json()["guidance"] == guidance
    assert too_long.status_code == 422
