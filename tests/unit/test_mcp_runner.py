"""The MCP runner: a real server on a real socket, running real (small) MCP server processes."""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest
import websockets
from mcp import Client
from mcp_runner.client import ExitReport, RunnerError, runner_stdio_transport
from mcp_runner.process import ServerProcess, build_environment
from mcp_runner.protocol import InvalidStart, validate_start
from mcp_runner.server import Capacity, create_app
from mcp_runner.settings import RunnerSettings
from mcp_runner.tools import SetupFailed, ToolInstaller
from uvicorn import Config, Server

SERVER = pathlib.Path(__file__).parents[1] / "support" / "minimal_mcp_server.py"
TOKEN = "t" * 24


def _settings(tmp_path: pathlib.Path, **overrides: Any) -> RunnerSettings:
    return RunnerSettings(
        token=TOKEN, cache_dir=tmp_path / "cache", work_dir=tmp_path / "work", **overrides
    )


@pytest.fixture
async def runner(tmp_path: pathlib.Path) -> AsyncIterator[tuple[str, RunnerSettings]]:
    settings = _settings(tmp_path)
    server = Server(Config(create_app(settings), host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn offers no readiness event
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", settings
    server.should_exit = True
    await task


def _command(script: str = str(SERVER)) -> str:
    return f"{sys.executable} {script}"


def _transport(url: str, command: str, env: dict[str, str] | None = None, token: str = TOKEN):
    return runner_stdio_transport(url, token, command, env or {}, org_id="org-1", server_id="srv-1")


async def test_tools_are_listed_and_called_through_the_runner(runner: tuple[str, Any]) -> None:
    url, _ = runner
    async with asyncio.timeout(20):
        async with Client(_transport(url, _command())) as client:
            assert [t.name for t in (await client.list_tools()).tools] == ["echo"]
            big = await client.call_tool("echo", {"text": "x" * 50_000})
            assert len(big.content[0].text) == len("echo: ") + 50_000  # type: ignore[union-attr]


async def test_a_wrong_token_is_refused(runner: tuple[str, Any]) -> None:
    url, _ = runner
    with pytest.raises(RunnerError, match="unreachable"):
        async with _transport(url, _command(), token="x" * 24):
            pass


async def test_an_invalid_start_is_explained_not_run(runner: tuple[str, Any]) -> None:
    url, _ = runner
    with pytest.raises(RunnerError, match="not a valid environment variable name"):
        async with _transport(url, _command(), env={"X-Org-ID": "1"}):
            pass


async def test_the_server_gets_its_configured_environment_and_never_the_runners_secrets(
    runner: tuple[str, Any], tmp_path: pathlib.Path
) -> None:
    url, _ = runner
    probe = tmp_path / "env_probe.py"
    probe.write_text(
        "import json,os,sys\n"
        "for raw in sys.stdin:\n"
        "    m=json.loads(raw)\n"
        "    if m.get('method')=='initialize':\n"
        "        seen={k:os.environ.get(k) for k in ('MY_TOKEN','MCP_RUNNER_TOKEN','HOME')}\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'protocolVersion':m['params']['protocolVersion'],"
        "'capabilities':{'tools':{}},'serverInfo':{'name':json.dumps(seen),'version':'1'}}}),flush=True)\n"
        "    elif m.get('id') is not None:\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{}}),flush=True)\n"
    )
    async with asyncio.timeout(20):
        async with Client(_transport(url, _command(str(probe)), {"MY_TOKEN": "s3cret"})) as client:
            seen = json.loads(client.server_info.name)
    assert seen["MY_TOKEN"] == "s3cret"
    assert seen["MCP_RUNNER_TOKEN"] is None
    assert seen["HOME"].endswith("org-1/srv-1")


async def test_a_command_that_dies_reports_why(runner: tuple[str, Any]) -> None:
    url, _ = runner
    report = ExitReport()
    transport = runner_stdio_transport(
        url, TOKEN, "echo boom >&2; exit 3", {}, org_id="o", server_id="s", exit_report=report
    )
    with pytest.raises(BaseException):  # noqa: B017 - the SDK wraps the closed pipe in a group
        async with asyncio.timeout(20):
            async with Client(transport):
                pass
    assert report.code == 3 and "boom" in report.stderr
    assert "boom" in report.describe()


async def test_closing_the_connection_ends_the_process(
    runner: tuple[str, Any], tmp_path: pathlib.Path
) -> None:
    url, _ = runner
    pidfile = tmp_path / "pid"
    command = f"echo $$ > {pidfile}; exec sleep 300"
    socket = await websockets.connect(
        url.replace("http", "ws") + "/v1/stdio",
        additional_headers={"Authorization": f"Bearer {TOKEN}"},
    )
    await socket.send(
        json.dumps({"t": "start", "command": command, "env": {}, "org_id": "o", "server_id": "s"})
    )
    assert json.loads(await socket.recv())["t"] == "ready"
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        await asyncio.sleep(0.05)
    pid = int(pidfile.read_text())
    await socket.close()

    for _ in range(100):
        try:
            import os

            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.05)
    pytest.fail("the process outlived its connection")


def test_capacity_is_bounded_in_total_and_per_organization() -> None:
    capacity = Capacity(total=3, per_org=2)
    assert capacity.try_acquire("a") and capacity.try_acquire("a")
    assert not capacity.try_acquire("a"), "one organization cannot take everything"
    assert capacity.try_acquire("b")
    assert not capacity.try_acquire("c"), "the runner is full"
    capacity.release("a")
    assert capacity.try_acquire("c")


def test_start_frames_are_validated() -> None:
    ok = {"command": "npx -y x", "env": {"A_B": "1"}, "org_id": "o", "server_id": "s"}
    assert validate_start(ok).command == "npx -y x"
    assert validate_start({**ok, "setup": "  make  "}).setup == "make"
    assert validate_start({**ok, "setup": "  "}).setup is None
    for bad in (
        {**ok, "command": "a\nb"},
        {**ok, "command": ""},
        {**ok, "env": {"1BAD": "x"}},
        {**ok, "env": {"PATH": "/evil"}},
        {**ok, "org_id": "../etc"},
        {**ok, "env": {"A": 1}},
        {**ok, "setup": "x" * 9000},
        {**ok, "setup": 5},
    ):
        with pytest.raises(InvalidStart):
            validate_start(bad)


def test_the_environment_is_built_from_scratch(tmp_path: pathlib.Path) -> None:
    env = build_environment(_settings(tmp_path), tmp_path / "w", {"K": "v"})
    assert env["K"] == "v" and env["npm_config_cache"].endswith("cache/npm")
    assert set(env) == {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "XDG_CACHE_HOME",
        "npm_config_cache",
        "UV_CACHE_DIR",
        "K",
    }


async def test_the_install_script_runs_once_per_organization_without_the_servers_secrets(
    tmp_path: pathlib.Path,
) -> None:
    settings = _settings(tmp_path)
    installer = ToolInstaller(settings)
    log = tmp_path / "log"
    script = f'echo "run key=${{API_KEY:-none}}" >> {log}; touch tool'

    first = await installer.ensure("org-a", script)
    again = await installer.ensure("org-a", script)
    other_org = await installer.ensure("org-b", script)

    assert first == again and first != other_org
    assert log.read_text().splitlines() == ["run key=none", "run key=none"], (
        "once per org, no secrets"
    )
    assert (first / "tool").exists()


async def test_concurrent_starts_install_only_once(tmp_path: pathlib.Path) -> None:
    installer = ToolInstaller(_settings(tmp_path))
    log = tmp_path / "log"
    script = f"sleep 0.3; echo x >> {log}"

    await asyncio.gather(*(installer.ensure("o", script) for _ in range(4)))

    assert log.read_text().count("x") == 1


async def test_a_failed_install_leaves_nothing_cached_and_says_why(tmp_path: pathlib.Path) -> None:
    installer = ToolInstaller(_settings(tmp_path))

    with pytest.raises(SetupFailed, match="no network"):
        await installer.ensure("o", "echo 'no network' >&2; exit 2")
    with pytest.raises(SetupFailed):
        await installer.ensure("o", "echo 'no network' >&2; exit 2")


async def test_a_program_installed_by_the_script_is_on_the_servers_path(
    tmp_path: pathlib.Path,
) -> None:
    settings = _settings(tmp_path)
    tools = await ToolInstaller(settings).ensure(
        "o", "printf '#!/bin/sh\\necho from-installed-tool\\n' > mytool; chmod +x mytool"
    )

    process = await ServerProcess.start("mytool", settings, tmp_path / "w", {}, tools)
    try:
        assert (await process.read_line()) == b"from-installed-tool\n"
    finally:
        await process.terminate()
