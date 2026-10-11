"""Pooled MCP connections and stdio servers that run in the session sandbox."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from api.services.mcp_runtime import McpServerTarget, McpToolProvider, build_targets
from api.services.sandbox_stdio import launch_script, parse_line, sandbox_stdio_transport
from config_engine.crypto import CredentialCipher
from mcp import Client
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult, PtyChannel

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


def test_stdio_servers_are_offered_only_with_a_running_sandbox_and_policy_consent() -> None:
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


async def test_stdio_servers_use_the_sandbox_connector_and_never_the_remote_one() -> None:
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


async def test_a_stdio_server_without_a_sandbox_is_skipped_not_crashed() -> None:
    from api.services import mcp_runtime

    mcp_runtime._listing_cache.clear()
    provider = await McpToolProvider(
        [_target("fs1", "Local FS", "stdio")], connector=_Connector(), guard=_noop_guard
    ).load()
    assert provider.definitions() == []


# --- the sandbox stdio transport ----------------------------------------------------------


def test_the_launch_script_quotes_everything_and_drops_invalid_env_names() -> None:
    script = launch_script(
        "python3 srv.py --name 'a b'",
        {"API_KEY": "s3'cret; rm -rf /", "bad name": "x", "1BAD": "y"},
        "/tmp/x.log",
        "/tmp/x.pid",
    )
    lines = script.splitlines()
    assert lines[0] == "stty raw -echo"
    assert "echo $$ > /tmp/x.pid" in lines[1]
    # The marker is never written out whole, or the terminal's echo of this line would trigger it.
    assert "__ROCKET_MCP_READY__" not in script
    assert (
        'printf "%s%s\\n" __ROCKET_M CP_READY__' in lines[1]
        and "'python3 srv.py --name '\"'\"'a b'\"'\"''" in lines[1]
    )
    assert "export API_KEY='s3'\"'\"'cret; rm -rf /'" in lines[1]
    assert "bad name" not in script and "1BAD" not in script
    assert lines[1].endswith("2>>/tmp/x.log")


@pytest.mark.parametrize(
    "line, kind",
    [
        (b"user@sandbox:/workspace$ stty raw -echo", None),
        (b"", None),
        (b'{"jsonrpc":"2.0","id":1,"result":{}}', "message"),
        (b'{"not": "jsonrpc"}', "error"),
        (b"{broken", "error"),
    ],
)
def test_terminal_noise_is_ignored_and_bad_json_is_surfaced(line: bytes, kind: str | None) -> None:
    parsed = parse_line(line)
    assert (
        None if parsed is None else "error" if isinstance(parsed, Exception) else "message"
    ) == kind


class _ServerPty(PtyChannel):
    """A terminal with an MCP server behind it: a banner first, then newline-delimited JSON-RPC."""

    def __init__(self) -> None:
        self.output: asyncio.Queue[bytes] = asyncio.Queue()
        self.received: list[dict[str, Any]] = []
        self.written_raw: list[bytes] = []
        self.closed = False
        self._partial = b""
        self.output.put_nowait(b"Welcome to the sandbox\r\nuser@box:/workspace$ ")
        self.started = False

    async def read(self) -> bytes:
        return await self.output.get()

    async def write(self, data: bytes) -> None:
        self.written_raw.append(data)
        if data.startswith(b"stty"):
            # The launch script: the replacement process announces itself once it is running.
            self.started = True
            await self.output.put(b"__ROCKET_MCP_READY__\r\n")
            return
        self._partial += data
        *lines, self._partial = self._partial.split(b"\n")
        for line in lines:
            message = json.loads(line)
            self.received.append(message)
            await self._answer(message)

    async def _answer(self, message: dict[str, Any]) -> None:
        method, request_id = message.get("method"), message.get("id")
        result: dict[str, Any] | None = None
        if method == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "t", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}
                ]
            }
        elif method == "tools/call":
            text = message["params"]["arguments"]["text"]
            result = {"content": [{"type": "text", "text": f"echo: {text}"}], "isError": False}
        elif request_id is not None:
            result = {}
        if result is not None and request_id is not None:
            await self.output.put(
                (json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n").encode()
            )

    async def resize(self, cols: int, rows: int) -> None:
        return None

    async def close(self) -> None:
        self.closed = True
        await self.output.put(b"")


class _PtyDriver(FakeSandboxDriver):
    def __init__(self, pty: _ServerPty) -> None:
        super().__init__()
        self.pty = pty
        self.cleanup: list[str] = []

    async def open_pty(self, session_id: str, cols: int = 80, rows: int = 24) -> PtyChannel:
        return self.pty

    async def exec_command(self, session_id: str, command: str, *a: Any, **k: Any) -> ExecResult:
        self.cleanup.append(command)
        return ExecResult(0, "", "", 1)


async def test_an_mcp_client_talks_to_a_server_through_the_sandbox_terminal() -> None:
    pty = _ServerPty()
    driver = _PtyDriver(pty)
    async with asyncio.timeout(20):
        async with Client(
            sandbox_stdio_transport(driver, "s1", "python3 srv.py", {"TOKEN": "t"})
        ) as client:
            tools = await client.list_tools()
            assert [t.name for t in tools.tools] == ["echo"]
            result = await client.call_tool("echo", {"text": "hello"})
            assert result.content[0].text == "echo: hello"  # type: ignore[union-attr]

    assert pty.written_raw[0].startswith(b"stty raw -echo\n")
    assert b"export TOKEN=t;" in pty.written_raw[0]
    assert b"'python3 srv.py'" in pty.written_raw[0]
    assert pty.closed
    assert any("kill -TERM -$pid" in c and "rm -f /tmp/rocket-mcp-" in c for c in driver.cleanup), (
        "the server's process group is ended explicitly"
    )
