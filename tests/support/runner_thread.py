"""A real MCP runner on a background thread, for tests that exercise it over its WebSocket."""

from __future__ import annotations

import pathlib
import threading
import time
from collections.abc import Callable

from mcp_runner.server import create_app
from mcp_runner.settings import RunnerSettings
from uvicorn import Config, Server

TOKEN = "integration-token-0123456789"


def start_runner(tmp_path: pathlib.Path) -> tuple[str, Callable[[], None]]:
    """``(url, stop)`` for a runner whose caches and scratch directories live under ``tmp_path``."""
    settings = RunnerSettings(token=TOKEN, cache_dir=tmp_path / "cache", work_dir=tmp_path / "work")
    server = Server(Config(create_app(settings), host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    url = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=10)

    return url, stop
