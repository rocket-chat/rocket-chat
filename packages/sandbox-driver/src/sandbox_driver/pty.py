"""Interactive terminals for the sandbox drivers.

Each driver hands back a :class:`PtyChannel`: raw bytes in and out plus window resizes. The
shell is bash when the image has it, else sh, started as a login shell in ``/workspace``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from sandbox_driver.blocking import run_blocking
from specifications.interfaces.sandbox import PtyChannel

SHELL_LAUNCHER = "if command -v bash >/dev/null 2>&1; then exec bash -l; else exec sh; fi"

_K8S_STDIN = b"\x00"
_K8S_RESIZE = b"\x04"
# Channels whose payload is terminal output (stdout/stderr share a TTY) vs. the exit status.
_K8S_OUTPUT_CHANNELS = (1, 2)
_K8S_ERROR_CHANNEL = 3


class DockerPty(PtyChannel):
    """A Docker exec TTY, read and written through its raw socket."""

    def __init__(self, client: Any, exec_id: str, sock: Any) -> None:
        self._client = client
        self._exec_id = exec_id
        self._sock = sock

    async def read(self) -> bytes:
        try:
            data: bytes = await asyncio.get_running_loop().sock_recv(self._sock, 4096)
        except (OSError, ConnectionError):
            return b""
        return data

    async def write(self, data: bytes) -> None:
        await asyncio.get_running_loop().sock_sendall(self._sock, data)

    async def resize(self, cols: int, rows: int) -> None:
        with contextlib.suppress(Exception):
            await run_blocking(self._client.api.exec_resize, self._exec_id, height=rows, width=cols)

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            self._sock.close()


class K8sPty(PtyChannel):
    """A Kubernetes exec TTY over the channel-prefixed WebSocket protocol."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def read(self) -> bytes:
        while True:
            message = await self._ws.receive()
            raw = getattr(message, "data", None)
            if getattr(message, "type", None) is not None and message.type.name in (
                "CLOSE",
                "CLOSING",
                "CLOSED",
                "ERROR",
            ):
                return b""
            if not raw:
                continue
            payload = raw.encode() if isinstance(raw, str) else bytes(raw)
            channel, body = payload[0], payload[1:]
            if channel in _K8S_OUTPUT_CHANNELS:
                if body:
                    return body
            elif channel == _K8S_ERROR_CHANNEL:
                return b""

    async def write(self, data: bytes) -> None:
        await self._ws.send_bytes(_K8S_STDIN + data)

    async def resize(self, cols: int, rows: int) -> None:
        frame = json.dumps({"Width": cols, "Height": rows}).encode()
        with contextlib.suppress(Exception):
            await self._ws.send_bytes(_K8S_RESIZE + frame)

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._ws.close()
