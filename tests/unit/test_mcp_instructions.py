"""What an MCP server says about itself reaches the administrator, and its tool docs the agent.

The server's ``instructions`` (MCP initialize result) are offered to start the agent guidance from;
its description of each tool and of each parameter is the tool definition the agent is given.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from api.main import app
from api.models import McpToolItem
from api.routers import mcp_servers
from api.services import mcp_runtime
from api.services.mcp_runtime import McpServerTarget, McpToolProvider, ProbedTools, probe_target
from fastapi.testclient import TestClient

ADMIN = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "admin_user", "X-User-Role": "admin"}

SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_id": {"type": "string", "description": "The invoice number, e.g. INV-1042"},
        "currency": {"type": "string", "description": "ISO 4217 code", "enum": ["EUR", "USD"]},
    },
    "required": ["invoice_id"],
}


def _connector(instructions: str | None) -> Any:
    @asynccontextmanager
    async def connect(_target: McpServerTarget) -> AsyncIterator[Any]:
        tool = SimpleNamespace(name="refund", description="Refund an invoice.", input_schema=SCHEMA)

        async def list_tools() -> Any:
            return SimpleNamespace(tools=[tool])

        yield SimpleNamespace(list_tools=list_tools, instructions=instructions)

    return connect


@pytest.fixture(autouse=True)
def public_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def reachable(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(mcp_runtime, "resolve_public_target", reachable)


TARGET = McpServerTarget("b", "Billing", "http", "https://billing.example.test/mcp")


async def test_a_probe_returns_the_servers_instructions_with_its_tools() -> None:
    status, tools, error = await probe_target(
        TARGET, 5, connector=_connector("  Always confirm the currency.\n")
    )

    assert (status, error) == ("connected", None)
    assert [t["name"] for t in tools] == ["refund"]
    assert isinstance(tools, ProbedTools) and tools.instructions == "Always confirm the currency."


@pytest.mark.parametrize("silent", [None, "", "   "])
async def test_a_server_without_instructions_has_none(silent: str | None) -> None:
    _, tools, _ = await probe_target(TARGET, 5, connector=_connector(silent))

    assert isinstance(tools, ProbedTools) and tools.instructions == ""


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def _validate(client: TestClient) -> dict[str, Any]:
    reply = client.post(
        "/v1/mcp/servers/validate",
        json={"transport": "http", "endpoint_or_command": "https://billing.example.test/mcp"},
        headers=ADMIN,
    )
    assert reply.status_code == 200
    return reply.json()


def test_validating_a_server_offers_its_instructions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def probe(**_: Any) -> Any:
        tools = ProbedTools([{"name": "refund", "description": "Refund"}], "Check the currency.")
        return "connected", tools, None

    monkeypatch.setattr(mcp_servers, "probe_and_fetch_tools", probe)

    reply = _validate(client)

    assert reply["valid"] is True and reply["instructions"] == "Check the currency."


def test_a_probe_that_returns_a_plain_list_offers_no_instructions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def probe(**_: Any) -> Any:
        return "connected", [{"name": "refund", "description": "Refund"}], None

    monkeypatch.setattr(mcp_servers, "probe_and_fetch_tools", probe)

    assert _validate(client)["instructions"] == ""


def test_what_a_server_says_is_cut_to_a_size_the_page_can_hold(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def probe(**_: Any) -> Any:
        return "connected", ProbedTools([], "x" * 100_000), None

    monkeypatch.setattr(mcp_servers, "probe_and_fetch_tools", probe)

    assert len(_validate(client)["instructions"]) == mcp_servers.MAX_SERVER_INSTRUCTIONS_CHARS


# -- the server's documentation of its tools and their parameters ------------------------------


async def test_the_agent_gets_the_servers_description_of_each_tool_and_parameter() -> None:
    async def reachable(_target: McpServerTarget) -> None:
        return None

    provider = McpToolProvider([TARGET], connector=_connector(None), guard=reachable)
    await provider.load()

    (definition,) = provider.definitions()

    assert definition.name == "mcp__billing__refund"
    assert definition.description == "[Billing] Refund an invoice."
    assert definition.parameters_json_schema["properties"]["invoice_id"]["description"] == (
        "The invoice number, e.g. INV-1042"
    )
    assert definition.parameters_json_schema["properties"]["currency"]["enum"] == ["EUR", "USD"]
    assert definition.parameters_json_schema["required"] == ["invoice_id"]
    await provider.aclose()


def test_a_tool_stored_with_the_retired_guidance_field_still_loads() -> None:
    legacy = McpToolItem(**{"name": "refund", "description": "Refund", "guidance": "old text"})

    assert not hasattr(legacy, "guidance"), "guidance is the server's, not each tool's"
