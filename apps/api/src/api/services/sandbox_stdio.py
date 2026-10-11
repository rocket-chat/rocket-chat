"""An MCP stdio transport whose server process runs *inside the session's sandbox*.

A stdio MCP server is an arbitrary command. Running it in the API process would hand a
connector author the control plane; running it in the sandbox gives it exactly what the agent
already has - the workspace, the network policy, the unprivileged user - and nothing more.

The sandbox driver offers a terminal, not a pipe, so the server is started on a pseudo-terminal
switched to raw mode (no echo, no newline translation), its stderr diverted to a log file, and
the newline-delimited JSON-RPC that stdio MCP speaks is carried over that terminal. Lines that
are not JSON (the shell's own banner and prompt) are ignored.
"""

from __future__ import annotations

import contextlib
import logging
import re
import shlex
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import anyio
from mcp import types
from mcp.client._transport import TransportStreams
from mcp.shared.message import SessionMessage

from specifications.interfaces.sandbox import PtyChannel, SandboxDriverProtocol

logger = logging.getLogger(__name__)

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
MAX_LINE_BYTES = 8 * 1024 * 1024
# Printed by the process that replaces the interactive shell. Until it is seen nothing is sent:
# the shell's line editor reads ahead, so a request written while it was still finishing its
# ``exec`` would be swallowed with the rest of its input buffer and never reach the server.
READY_MARKER = "__ROCKET_MCP_READY__"
READY_TIMEOUT_SECONDS = 30.0


def launch_script(command: str, env: Mapping[str, str], log_file: str, pid_file: str) -> str:
    """The shell input that puts the terminal in raw mode and ``exec``s the server.

    The shell records its PID first. ``exec`` keeps that PID, and a terminal's first process
    leads its own process group, so ``kill -<pid>`` (a negative PID) later reaches the server and any
    children it started.
    """
    exports = "".join(
        f"export {name}={shlex.quote(value)}; "
        for name, value in env.items()
        if _ENV_NAME.match(name)
    )
    # The marker is assembled by printf so the terminal's echo of this very line (it is still in
    # cooked mode while the line is typed) can never be mistaken for the server being ready.
    head, tail = READY_MARKER[: len(READY_MARKER) // 2], READY_MARKER[len(READY_MARKER) // 2 :]
    inner = f'printf "%s%s\\n" {head} {tail}; exec sh -c "$1"'
    return (
        "stty raw -echo\n"
        f"echo $$ > {shlex.quote(pid_file)}; "
        f"{exports}exec sh -c {shlex.quote(inner)} sh {shlex.quote(command)} "
        f"2>>{shlex.quote(log_file)}\n"
    )


def parse_line(line: bytes) -> SessionMessage | Exception | None:
    """A JSON-RPC message, a parse error to surface, or ``None`` for terminal noise."""
    text = line.decode("utf-8", errors="replace").strip()
    if not text.startswith("{"):
        return None
    try:
        return SessionMessage(types.jsonrpc_message_adapter.validate_json(text, by_name=False))
    except ValueError as err:
        return err


@asynccontextmanager
async def sandbox_stdio_transport(
    driver: SandboxDriverProtocol,
    session_id: str,
    command: str,
    env: Mapping[str, str] | None = None,
) -> AsyncIterator[TransportStreams]:
    channel: PtyChannel = await driver.open_pty(session_id, cols=500, rows=50)
    tag = uuid.uuid4().hex[:8]
    log_file, pid_file = f"/tmp/rocket-mcp-{tag}.log", f"/tmp/rocket-mcp-{tag}.pid"
    read_send, read_recv = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_send, write_recv = anyio.create_memory_object_stream[SessionMessage](0)

    ready = anyio.Event()

    async def pump_output() -> None:
        buffer = b""
        try:
            async with read_send:
                while True:
                    chunk = await channel.read()
                    if not chunk:
                        return
                    buffer += chunk
                    if len(buffer) > MAX_LINE_BYTES:
                        logger.warning("MCP stdio server wrote an oversized line; dropping it")
                        buffer = b""
                    *lines, buffer = buffer.split(b"\n")
                    for line in lines:
                        if READY_MARKER.encode() in line:
                            ready.set()
                            continue
                        if not ready.is_set():
                            continue
                        parsed = parse_line(line)
                        if parsed is not None:
                            await read_send.send(parsed)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            return

    async def pump_input() -> None:
        try:
            with anyio.fail_after(READY_TIMEOUT_SECONDS):
                await ready.wait()
        except TimeoutError:
            logger.warning("MCP stdio server in %s never became ready", session_id)
            await read_send.aclose()
            return
        try:
            async with write_recv:
                async for message in write_recv:
                    payload = message.message.model_dump_json(by_alias=True, exclude_unset=True)
                    await channel.write((payload + "\n").encode("utf-8"))
        except (anyio.ClosedResourceError, anyio.BrokenResourceError, OSError):
            await read_send.aclose()

    try:
        await channel.write(launch_script(command, env or {}, log_file, pid_file).encode("utf-8"))
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(pump_output)
            tasks.start_soon(pump_input)
            try:
                yield read_recv, write_send
            finally:
                tasks.cancel_scope.cancel()
    finally:
        # Raw mode turns off Ctrl-C, and closing a terminal does not stop what runs on it, so
        # the server's process group is ended explicitly.
        with contextlib.suppress(Exception):
            await driver.exec_command(
                session_id,
                f'pid=$(cat {pid_file} 2>/dev/null) && [ -n "$pid" ] && '
                f"{{ kill -TERM -$pid; sleep 0.3; kill -KILL -$pid; }} 2>/dev/null; "
                f"rm -f {pid_file}",
                timeout_seconds=15,
            )
        with contextlib.suppress(Exception):
            await channel.close()
        await read_recv.aclose()
        await write_send.aclose()
