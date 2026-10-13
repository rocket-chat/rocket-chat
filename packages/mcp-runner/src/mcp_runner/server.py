"""The runner's HTTP surface: a health probe and the WebSocket that carries one MCP server."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import time
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio.to_thread
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from mcp_runner.process import ServerProcess
from mcp_runner.protocol import START_TIMEOUT_SECONDS, InvalidStart, StartRequest, validate_start
from mcp_runner.settings import RunnerSettings
from mcp_runner.tools import SetupFailed, ToolInstaller, executable_directories

logger = logging.getLogger(__name__)

CLOSE_UNAUTHORIZED = 4401
CLOSE_BUSY = 4429
EXIT_REAP_SECONDS = 5.0


class Capacity:
    """How many servers run, in total and per organization."""

    def __init__(self, total: int, per_org: int) -> None:
        self._total, self._per_org = total, per_org
        self._by_org: Counter[str] = Counter()

    @property
    def running(self) -> int:
        return sum(self._by_org.values())

    def try_acquire(self, org_id: str) -> bool:
        if self.running >= self._total or self._by_org[org_id] >= self._per_org:
            return False
        self._by_org[org_id] += 1
        return True

    def release(self, org_id: str) -> None:
        self._by_org[org_id] -= 1
        if self._by_org[org_id] <= 0:
            del self._by_org[org_id]


def _authorised(websocket: WebSocket, token: str) -> bool:
    header = websocket.headers.get("authorization", "")
    presented = header.removeprefix("Bearer ").strip()
    # Digests of equal length, so the comparison does not reveal where the tokens differ.
    return hmac.compare_digest(
        hashlib.sha256(presented.encode()).digest(), hashlib.sha256(token.encode()).digest()
    )


async def _send(websocket: WebSocket, frame: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(frame, separators=(",", ":")))


async def _read_start(websocket: WebSocket) -> StartRequest:
    raw = await asyncio.wait_for(websocket.receive_text(), START_TIMEOUT_SECONDS)
    try:
        frame = json.loads(raw)
    except ValueError as err:
        raise InvalidStart("The first frame must be JSON.") from err
    if not isinstance(frame, dict) or frame.get("t") != "start":
        raise InvalidStart("The first frame must be a start frame.")
    return validate_start(frame)


async def _stdout_to_socket(process: ServerProcess, websocket: WebSocket) -> None:
    while (line := await process.read_line()) is not None:
        text = line.decode("utf-8", errors="replace").strip()
        # MCP over stdio is newline-delimited JSON; anything else is the package manager talking.
        if text.startswith("{"):
            await _send(websocket, {"t": "msg", "line": text})


async def _socket_to_stdin(process: ServerProcess, websocket: WebSocket) -> None:
    while True:
        try:
            frame = json.loads(await websocket.receive_text())
        except ValueError:
            continue
        if (
            isinstance(frame, dict)
            and frame.get("t") == "msg"
            and isinstance(frame.get("line"), str)
        ):
            await process.write_line(frame["line"])


async def _serve(
    websocket: WebSocket, settings: RunnerSettings, installer: ToolInstaller, start: StartRequest
) -> None:
    tool_paths: list[Path] = []
    if start.setup:
        await _send(websocket, {"t": "status", "message": "Installing the server's program..."})
        tool_dir = await installer.ensure(start.org_id, start.setup)
        tool_paths = await anyio.to_thread.run_sync(executable_directories, tool_dir)
    workdir = settings.work_dir / start.org_id / start.server_id
    process = await ServerProcess.start(start.command, settings, workdir, start.env, tool_paths)
    await _send(websocket, {"t": "ready"})
    output = asyncio.create_task(_stdout_to_socket(process, websocket))
    pumps = [output, asyncio.create_task(_socket_to_stdin(process, websocket))]
    exited = asyncio.create_task(process.wait())
    try:
        done, _ = await asyncio.wait(
            [exited, *pumps],
            timeout=settings.max_lifetime_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        # A server that dies closes its output before the process is reaped, so either one means
        # it ended; the exit frame carries why.
        if exited in done or output in done:
            code = await asyncio.wait_for(asyncio.shield(exited), EXIT_REAP_SECONDS)
            # Whatever the server printed before it ended still goes out, then the exit frame.
            await asyncio.gather(output, return_exceptions=True)
            await _send(websocket, {"t": "exit", "code": code, "stderr": process.stderr_tail})
    finally:
        for task in (*pumps, exited):
            task.cancel()
        await process.terminate()


def create_app(settings: RunnerSettings | None = None) -> FastAPI:
    config = settings or RunnerSettings.from_env()
    capacity = Capacity(config.max_processes, config.max_processes_per_org)
    installer = ToolInstaller(config)
    started = time.time()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        for sub in ("npm", "uv", "xdg"):
            (config.cache_dir / sub).mkdir(parents=True, exist_ok=True)
        config.work_dir.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title="Rocket Chat MCP runner", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "running": capacity.running,
            "uptime_seconds": int(time.time() - started),
        }

    @app.websocket("/v1/stdio")
    async def stdio(websocket: WebSocket) -> None:
        if not _authorised(websocket, config.token):
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return
        await websocket.accept()
        acquired: str | None = None
        try:
            start = await _read_start(websocket)
            if not capacity.try_acquire(start.org_id):
                await _send(
                    websocket,
                    {"t": "error", "message": "The runner is at capacity; try again shortly."},
                )
                await websocket.close(code=CLOSE_BUSY)
                return
            acquired = start.org_id
            await _serve(websocket, config, installer, start)
        except (InvalidStart, SetupFailed) as err:
            await _send(websocket, {"t": "error", "message": str(err)})
        except (WebSocketDisconnect, TimeoutError):
            pass
        except OSError as err:
            with contextlib.suppress(Exception):
                await _send(
                    websocket, {"t": "error", "message": f"Could not start the command: {err}"}
                )
        finally:
            if acquired is not None:
                capacity.release(acquired)
            with contextlib.suppress(Exception):
                await websocket.close()

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8100, log_level="info")
