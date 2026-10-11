"""MCP server tools are callable by the agent, per caller and per organization."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.services import mcp_runtime
from api.services.mcp_runtime import McpServerTarget, McpToolProvider, build_targets
from config_engine.crypto import CredentialCipher
from mcp import Client
from mcp.server.mcpserver import MCPServer

from specifications.interfaces.agent import TurnCallbacks
from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk


def _fixture_server(name: str = "Weather") -> MCPServer:
    server = MCPServer(name)

    @server.tool()
    def forecast(city: str, days: int = 1) -> str:
        """Forecast for a city."""
        return f"{city}: sunny for {days} day(s)"

    @server.tool()
    def explode() -> str:
        """Always fails."""
        raise RuntimeError("kaboom")

    return server


def _connector(servers: dict[str, MCPServer]) -> Any:
    def connect(target: McpServerTarget) -> AbstractAsyncContextManager[Client]:
        return Client(servers[target.server_id])

    return connect


async def _no_guard(_: McpServerTarget) -> None:
    return None


def _target(server_id: str = "srv1", name: str = "Weather", updated_at: int = 1) -> McpServerTarget:
    return McpServerTarget(server_id, name, "http", "https://mcp.example.test/mcp", updated_at)


@pytest.fixture(autouse=True)
def _clear_listing_cache() -> None:
    mcp_runtime._listing_cache.clear()


async def _provider(*servers: tuple[McpServerTarget, MCPServer]) -> McpToolProvider:
    return await McpToolProvider(
        [t for t, _ in servers],
        connector=_connector({t.server_id: s for t, s in servers}),
        guard=_no_guard,
    ).load()


@pytest.mark.asyncio
async def test_server_tools_are_listed_namespaced_with_their_real_schema() -> None:
    provider = await _provider((_target(), _fixture_server()))
    by_name = {d.name: d for d in provider.definitions()}

    assert set(by_name) == {"mcp__weather__forecast", "mcp__weather__explode"}
    forecast = by_name["mcp__weather__forecast"]
    assert "[Weather]" in forecast.description
    assert forecast.parameters_json_schema["properties"]["city"]["type"] == "string"
    assert provider.owns("mcp__weather__forecast") and not provider.owns("file_read")


@pytest.mark.asyncio
async def test_calling_a_tool_returns_its_text_and_errors_are_reported_not_raised() -> None:
    provider = await _provider((_target(), _fixture_server()))

    assert await provider.call("mcp__weather__forecast", {"city": "Oslo", "days": 2}) == (
        "Oslo: sunny for 2 day(s)"
    )
    failure = await provider.call("mcp__weather__explode", {})
    assert failure.startswith("MCP tool error:") and "explode" in failure


@pytest.mark.asyncio
async def test_two_servers_with_the_same_name_get_distinct_namespaces() -> None:
    first, second = _target("aaaa1111", "Weather"), _target("bbbb2222", "Weather")
    provider = await _provider((first, _fixture_server()), (second, _fixture_server()))

    names = [d.name for d in provider.definitions()]
    assert len(names) == len(set(names)) == 4


@pytest.mark.asyncio
async def test_unreachable_server_is_skipped_without_breaking_the_turn() -> None:
    def broken(_: McpServerTarget) -> AbstractAsyncContextManager[Client]:
        raise ConnectionError("refused")

    provider = await McpToolProvider([_target()], connector=broken, guard=_no_guard).load()
    assert provider.definitions() == []


@pytest.mark.asyncio
async def test_private_server_urls_are_refused_before_connecting() -> None:
    connected: list[str] = []

    def connect(target: McpServerTarget) -> AbstractAsyncContextManager[Client]:
        connected.append(target.url)
        return Client(_fixture_server())

    internal = McpServerTarget("srv1", "Internal", "http", "http://169.254.169.254/mcp")
    provider = await McpToolProvider([internal], connector=connect).load()

    assert provider.definitions() == [] and connected == []


def test_targets_follow_the_organization_transport_policy() -> None:
    cipher = CredentialCipher()
    rows = [
        {
            "id": "a",
            "name": "A",
            "transport": "http",
            "scope": "org",
            "endpoint_or_command": "https://a/mcp",
        },
        {
            "id": "b",
            "name": "B",
            "transport": "sse",
            "scope": "user",
            "endpoint_or_command": "https://b/sse",
        },
        {
            "id": "c",
            "name": "C",
            "transport": "stdio",
            "scope": "org",
            "endpoint_or_command": "npx evil",
        },
        {
            "id": "d",
            "name": "D",
            "transport": "streamable_http",
            "scope": "org",
            "endpoint_or_command": "https://d/mcp",
        },
        {
            "id": "e",
            "name": "E",
            "transport": "http",
            "scope": "org",
            "endpoint_or_command": "https://e",
            "is_builtin": True,
        },
    ]
    targets = build_targets(
        rows, cipher=cipher, allowed_org_transports=["http"], allowed_user_transports=["http"]
    )
    # B is user-scoped over sse, which the user policy forbids; C is stdio; E is a built-in.
    assert [t.server_id for t in targets] == ["a", "d"]


def test_stored_credentials_become_request_headers() -> None:
    cipher = CredentialCipher()
    row = {
        "id": "a",
        "name": "A",
        "transport": "http",
        "scope": "org",
        "endpoint_or_command": "https://a/mcp",
        "encrypted_auth": {
            "api_key": cipher.encrypt("sk-secret"),
            "headers": [
                {"name": "X-Team", "value": "core", "is_secret": False},
                {"name": "X-Token", "value": cipher.encrypt("t0k"), "is_secret": True},
            ],
        },
    }
    (target,) = build_targets(
        [row], cipher=cipher, allowed_org_transports=["http"], allowed_user_transports=[]
    )
    assert target.headers == {
        "Authorization": "Bearer sk-secret",
        "X-Team": "core",
        "X-Token": "t0k",
    }
    assert "sk-secret" not in repr(target), "secrets must not leak through repr/logging"


def _turn(provider: McpToolProvider | None, **overrides: Any) -> ExecutionContext:
    import asyncio

    return ExecutionContext(
        session_id="s1",
        event_queue=asyncio.Queue(),
        parent_model="m",
        callbacks=TurnCallbacks(external_tools=provider),
        **overrides,
    )


@pytest.mark.asyncio
async def test_registry_exposes_and_routes_tools_only_inside_the_owning_turn() -> None:
    registry = ToolRegistry()
    provider = await _provider((_target(), _fixture_server()))

    token = current_execution_ctx.set(_turn(provider))
    try:
        assert "mcp__weather__forecast" in {d.name for d in registry.get_tool_definitions()}
        assert await registry.call_tool("mcp__weather__forecast", {"city": "Rome"}) == (
            "Rome: sunny for 1 day(s)"
        )
    finally:
        current_execution_ctx.reset(token)

    other = current_execution_ctx.set(_turn(None))
    try:
        assert "mcp__weather__forecast" not in {d.name for d in registry.get_tool_definitions()}
    finally:
        current_execution_ctx.reset(other)


@pytest.mark.asyncio
async def test_plan_mode_and_scope_allowlists_apply_to_mcp_tools() -> None:
    registry = ToolRegistry()
    provider = await _provider((_target(), _fixture_server()))

    token = current_execution_ctx.set(_turn(provider, plan_mode=True))
    try:
        assert (await registry.call_tool("mcp__weather__forecast", {"city": "x"})).startswith(
            "Blocked"
        )
    finally:
        current_execution_ctx.reset(token)

    token = current_execution_ctx.set(_turn(provider, allowed_tools=frozenset({"file_read"})))
    try:
        assert (await registry.call_tool("mcp__weather__forecast", {"city": "x"})).startswith(
            "Blocked"
        )
    finally:
        current_execution_ctx.reset(token)


@dataclass
class _CallsTheMcpTool(LLMGatewayProtocol):
    requests: list[Any] = field(default_factory=list)

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        results = [m.content for m in request.messages if m.role == "tool"]
        if not results and request.tools:
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call_1",
                    "function": {
                        "name": "mcp__weather__forecast",
                        "arguments": json.dumps({"city": "Paris", "days": 3}),
                    },
                }
            )
            return
        yield StreamChunk(text_delta=f"DONE {results[0] if results else ''}")

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_the_agent_loop_calls_an_mcp_tool_end_to_end() -> None:
    gateway = _CallsTheMcpTool()
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=ToolRegistry())
    provider = await _provider((_target(), _fixture_server()))

    events = [
        event
        async for event in orchestrator.process_user_turn(
            "s-mcp",
            "weather in Paris?",
            callbacks=TurnCallbacks(external_tools=provider),
        )
    ]

    advertised = {t.name for t in gateway.requests[0].tools}
    assert "mcp__weather__forecast" in advertised
    tool_messages = [m.content for m in gateway.requests[-1].messages if m.role == "tool"]
    assert tool_messages == ["Paris: sunny for 3 day(s)"], "the model saw the MCP result"
    assert any(e.event_type == "tool_call" for e in events)


@pytest.mark.asyncio
async def test_a_turn_only_sees_its_own_organizations_and_members_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import mcp_turn

    def record(server_id: str, org: str, scope: str, user: str | None) -> dict[str, Any]:
        return {
            "id": server_id,
            "org_id": org,
            "name": server_id,
            "transport": "http",
            "scope": scope,
            "user_id": user,
            "endpoint_or_command": f"https://{server_id}.example.test/mcp",
        }

    monkeypatch.setattr(
        mcp_turn,
        "_mcp_servers_store",
        {
            "org-a-shared": record("org-a-shared", "org_a", "org", None),
            "org-a-alice": record("org-a-alice", "org_a", "user", "alice"),
            "org-a-bob": record("org-a-bob", "org_a", "user", "bob"),
            "org-b-shared": record("org-b-shared", "org_b", "org", None),
        },
    )
    seen: list[list[str]] = []

    class Recorder:
        def __init__(self, targets: list[McpServerTarget]) -> None:
            seen.append(sorted(t.server_id for t in targets))

        async def load(self) -> Recorder:
            return self

        def definitions(self) -> list[Any]:
            return [object()]

    monkeypatch.setattr(mcp_turn, "McpToolProvider", Recorder)
    monkeypatch.setattr(
        mcp_turn,
        "effective_settings",
        _fixed_policy,
    )
    await mcp_turn._mcp_tools_for_turn(None, "org_a", "alice")

    assert seen == [["org-a-alice", "org-a-shared"]]


async def _fixed_policy(*_: Any, **__: Any) -> dict[str, Any]:
    return {"allowed_transports": ["http"], "user_allowed_transports": ["http"]}
