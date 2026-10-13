"""Pooled MCP connections, and which command (stdio) servers a turn may use."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from api.services.mcp_runtime import McpServerTarget, McpToolProvider, build_targets
from config_engine.crypto import CredentialCipher

# --- build_targets and policy -------------------------------------------------------------

ROWS = [
    {
        "id": "r1",
        "name": "Weather",
        "transport": "http",
        "scope": "org",
        "endpoint_or_command": "https://w.example/mcp",
    },
    {
        "id": "s1",
        "name": "Local FS",
        "transport": "stdio",
        "scope": "org",
        "endpoint_or_command": "npx -y fs-server /workspace",
    },
    {
        "id": "s2",
        "name": "Mine",
        "transport": "stdio",
        "scope": "user",
        "endpoint_or_command": "python3 mine.py",
    },
    {
        "id": "s3",
        "name": "Bad",
        "transport": "stdio",
        "scope": "org",
        "endpoint_or_command": "ok\nrm -rf /",
    },
]


def _names(**kw: Any) -> set[str]:
    targets = build_targets(ROWS, cipher=CredentialCipher(), **kw)
    return {t.name for t in targets}


def test_stdio_servers_are_offered_only_with_a_runner_and_policy_consent() -> None:
    allow_all = {"allowed_org_transports": ["http", "stdio"], "allowed_user_transports": ["stdio"]}
    assert _names(**allow_all) == {"Weather"}, "no sandbox yet"
    assert _names(**allow_all, stdio_available=True) == {"Weather", "Local FS", "Mine"}
    assert _names(
        allowed_org_transports=["http"], allowed_user_transports=[], stdio_available=True
    ) == {"Weather"}, "the organization has not allowed stdio"
    assert _names(
        allowed_org_transports=["stdio"], allowed_user_transports=[], stdio_available=True
    ) == {"Local FS"}, "members have not been allowed their own stdio servers"


# --- pooling ------------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, owner: _Connector) -> None:
        self.owner = owner

    async def list_tools(self) -> Any:
        return SimpleNamespace(
            tools=[
                SimpleNamespace(name="echo", description="Echo", input_schema={"type": "object"})
            ]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.owner.calls += 1
        if self.owner.fail_next > 0:
            self.owner.fail_next -= 1
            raise ConnectionResetError("connection dropped")
        return SimpleNamespace(content=[SimpleNamespace(text=f"ok {arguments}")], is_error=False)


class _Connector:
    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0
        self.calls = 0
        self.fail_next = 0

    @asynccontextmanager
    async def __call__(self, target: McpServerTarget) -> AsyncIterator[_FakeClient]:
        self.opened += 1
        try:
            yield _FakeClient(self)
        finally:
            self.closed += 1


async def _noop_guard(target: McpServerTarget) -> None:
    return None


def _target(server_id: str, name: str, transport: str = "http") -> McpServerTarget:
    return McpServerTarget(
        server_id=server_id,
        name=name,
        transport=transport,
        url="https://x.example/mcp" if transport != "stdio" else "cmd",
    )


async def _provider(connector: _Connector, **kw: Any) -> McpToolProvider:
    from api.services import mcp_runtime

    mcp_runtime._listing_cache.clear()
    provider = McpToolProvider(
        [_target("srv1", "Weather")], connector=connector, guard=_noop_guard, **kw
    )
    return await provider.load()


async def test_one_connection_serves_the_listing_and_every_call_of_a_turn() -> None:
    connector = _Connector()
    provider = await _provider(connector)
    for _ in range(3):
        assert (await provider.call("mcp__weather__echo", {"a": 1})).startswith("ok")
    assert connector.opened == 1
    await provider.aclose()
    assert connector.closed == 1


async def test_a_dropped_connection_is_reopened_once_and_the_call_succeeds() -> None:
    connector = _Connector()
    provider = await _provider(connector)
    connector.fail_next = 1
    assert (await provider.call("mcp__weather__echo", {})).startswith("ok")
    assert connector.opened == 2 and connector.closed == 1


async def test_a_server_that_keeps_failing_reports_the_error_instead_of_looping() -> None:
    connector = _Connector()
    provider = await _provider(connector)
    connector.fail_next = 5
    result = await provider.call("mcp__weather__echo", {})
    assert result.startswith("MCP tool error: could not call")
    assert connector.calls == 2


async def test_closing_twice_and_closing_an_unused_provider_are_harmless() -> None:
    provider = await _provider(_Connector())
    await provider.aclose()
    await provider.aclose()


async def test_stdio_servers_use_the_runner_connector_and_never_the_remote_one() -> None:
    from api.services import mcp_runtime

    mcp_runtime._listing_cache.clear()
    remote, sandbox = _Connector(), _Connector()
    provider = McpToolProvider(
        [_target("fs1", "Local FS", "stdio")],
        connector=remote,
        stdio_connector=sandbox,
        guard=_noop_guard,
    )
    await provider.load()
    assert (await provider.call("mcp__local-fs__echo", {})).startswith("ok")
    assert (remote.opened, sandbox.opened) == (0, 1)


async def test_a_stdio_server_without_a_runner_is_skipped_not_crashed() -> None:
    from api.services import mcp_runtime

    mcp_runtime._listing_cache.clear()
    provider = await McpToolProvider(
        [_target("fs1", "Local FS", "stdio")], connector=_Connector(), guard=_noop_guard
    ).load()
    assert provider.definitions() == []


# --- the sandbox stdio transport ----------------------------------------------------------
