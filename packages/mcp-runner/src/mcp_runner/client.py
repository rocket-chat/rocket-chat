"""The control plane's side: an MCP transport that talks to a server running on a runner."""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

import anyio
import websockets
from mcp import types
from mcp.client._transport import TransportStreams
from mcp.shared.message import SessionMessage
from websockets.asyncio.client import ClientConnection

from mcp_runner.protocol import MAX_LINE_BYTES, START_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 15.0
SETUP_WAIT_SECONDS = 660.0


@dataclass
class ExitReport:
    """Why the server process ended, filled in by the transport (the MCP SDK only sees a closed pipe)."""

    code: int | None = None
    stderr: str = ""

    def describe(self) -> str:
        return (
            f"The server exited ({self.code}). {self.stderr}".strip()
            if self.code is not None
            else ""
        )


class RunnerError(RuntimeError):
    """The runner refused or could not start the server (its message says why)."""


def websocket_url(base_url: str) -> str:
    scheme = "wss" if base_url.startswith("https") else "ws"
    return f"{scheme}://{base_url.split('://', 1)[-1].rstrip('/')}/v1/stdio"


async def _await_ready(socket: ClientConnection, installing: bool) -> None:
    # An install script can take minutes; the runner reports progress while it works.
    limit = SETUP_WAIT_SECONDS if installing else START_TIMEOUT_SECONDS + CONNECT_TIMEOUT_SECONDS
    with anyio.fail_after(limit):
        async for raw in socket:
            frame = json.loads(raw)
            if frame.get("t") == "ready":
                return
            if frame.get("t") == "error":
                raise RunnerError(str(frame.get("message") or "The runner refused the server."))
    raise RunnerError("The runner closed the connection before the server started.")


async def _collect_exit(socket: ClientConnection, report: ExitReport) -> None:
    """The exit frame can still be in flight when the MCP session fails on the closed pipe."""
    with anyio.move_on_after(0.3), contextlib.suppress(websockets.ConnectionClosed):
        async for raw in socket:
            frame = json.loads(raw)
            if frame.get("t") == "exit":
                report.code = frame.get("code")
                report.stderr = str(frame.get("stderr") or "").strip()
                return


def _parse(line: str) -> SessionMessage | Exception:
    try:
        return SessionMessage(types.jsonrpc_message_adapter.validate_json(line, by_name=False))
    except ValueError as err:
        return err


@asynccontextmanager
async def runner_stdio_transport(
    base_url: str,
    token: str,
    command: str,
    env: Mapping[str, str],
    *,
    org_id: str,
    server_id: str,
    setup: str | None = None,
    exit_report: ExitReport | None = None,
) -> AsyncIterator[TransportStreams]:
    """Start ``command`` on the runner and yield the MCP read/write streams for it."""
    read_send, read_recv = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_send, write_recv = anyio.create_memory_object_stream[SessionMessage](0)
    try:
        socket = await websockets.connect(
            websocket_url(base_url),
            additional_headers={"Authorization": f"Bearer {token}"},
            max_size=MAX_LINE_BYTES + 4096,
            open_timeout=CONNECT_TIMEOUT_SECONDS,
        )
    except (OSError, websockets.WebSocketException, TimeoutError) as err:
        raise RunnerError(f"The MCP runner is unreachable: {err}") from err

    async def pump_output() -> None:
        try:
            async with read_send:
                async for raw in socket:
                    frame = json.loads(raw)
                    if frame.get("t") == "msg":
                        await read_send.send(_parse(frame["line"]))
                    elif frame.get("t") == "exit":
                        if exit_report is not None:
                            exit_report.code = frame.get("code")
                            exit_report.stderr = str(frame.get("stderr") or "").strip()
                        logger.info(
                            "MCP server %s exited with %s: %s",
                            server_id,
                            frame.get("code"),
                            str(frame.get("stderr") or "")[-300:],
                        )
                        return
        except (websockets.ConnectionClosed, anyio.ClosedResourceError, anyio.BrokenResourceError):
            return

    async def pump_input() -> None:
        try:
            async with write_recv:
                async for message in write_recv:
                    payload = message.message.model_dump_json(by_alias=True, exclude_unset=True)
                    await socket.send(json.dumps({"t": "msg", "line": payload}))
        except (websockets.ConnectionClosed, anyio.ClosedResourceError, anyio.BrokenResourceError):
            return

    try:
        await socket.send(
            json.dumps(
                {
                    "t": "start",
                    "command": command,
                    "env": dict(env),
                    "org_id": org_id,
                    "server_id": server_id,
                    "setup": setup,
                }
            )
        )
        await _await_ready(socket, installing=bool(setup))
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(pump_output)
            tasks.start_soon(pump_input)
            try:
                yield read_recv, write_send
            finally:
                tasks.cancel_scope.cancel()
    finally:
        if exit_report is not None and exit_report.code is None:
            await _collect_exit(socket, exit_report)
        # Closing the socket is what ends the process on the runner.
        with contextlib.suppress(Exception):
            await socket.close()
        await read_recv.aclose()
        await write_send.aclose()
