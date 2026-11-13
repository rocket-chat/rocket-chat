"""The sandbox MCP over HTTP: mounted in the API, behind its authentication, stateless.

JSON-RPC requests as an MCP client sends them, against the real application with in-memory
stores and the fake sandbox driver: the caller is whoever the request authenticates as.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from api.services.settings import save_org_config
from fastapi.testclient import TestClient

from specifications.interfaces.sandbox_mcp import SANDBOX_MCP_PATH

_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _as(org: str, user: str) -> dict[str, str]:
    return {
        **_MCP_HEADERS,
        "X-Tenant-Org-Id": org,
        "X-Tenant-User-Id": user,
        "X-User-Role": "member",
    }


def _call(
    client: TestClient, headers: dict[str, str], tool: str, **arguments: Any
) -> dict[str, Any]:
    response = client.post(
        f"{SANDBOX_MCP_PATH}/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


def _text(result: dict[str, Any]) -> str:
    return "".join(block.get("text", "") for block in result["content"])


def test_an_agent_opens_a_sandbox_and_runs_a_command_over_http(client: TestClient) -> None:
    org = f"org_mcp_http_{uuid.uuid4().hex[:8]}"
    asyncio.run(save_org_config(None, org, "sandboxes", {"mcp_enabled": True}, []))
    alice = _as(org, "alice")

    listed = client.post(
        f"{SANDBOX_MCP_PATH}/",
        headers=alice,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert {"sandbox_open", "bash_exec", "git_push"} <= names

    opened = _call(client, alice, "sandbox_open", title="over http")
    assert not opened.get("isError"), opened
    sandbox_id = json.loads(_text(opened))["sandbox_id"]

    ran = _call(client, alice, "bash_exec", sandbox_id=sandbox_id, command="echo hi", cwd=".")
    assert not ran.get("isError"), ran

    session = client.get(f"/v1/sessions/{sandbox_id}", headers=alice)
    assert session.status_code == 200
    assert session.json()["title"] == "over http"


def test_another_member_cannot_act_in_the_sandbox(client: TestClient) -> None:
    org = f"org_mcp_http_{uuid.uuid4().hex[:8]}"
    asyncio.run(save_org_config(None, org, "sandboxes", {"mcp_enabled": True}, []))
    opened = _call(client, _as(org, "alice"), "sandbox_open")
    sandbox_id = json.loads(_text(opened))["sandbox_id"]

    refused = _call(client, _as(org, "mallory"), "env_list", sandbox_id=sandbox_id)

    assert refused.get("isError")
    assert "No sandbox" in _text(refused)


def test_the_mcp_answers_an_organization_that_did_not_enable_it_with_a_refusal(
    client: TestClient,
) -> None:
    org = f"org_mcp_http_{uuid.uuid4().hex[:8]}"

    refused = _call(client, _as(org, "alice"), "sandbox_types")

    assert refused.get("isError")
    assert "turned off" in _text(refused)
