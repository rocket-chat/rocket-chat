"""One MCP server process: a clean environment, its own process group, bounded output."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path

import anyio

from mcp_runner.protocol import MAX_LINE_BYTES
from mcp_runner.settings import RunnerSettings

STDERR_TAIL_BYTES = 4096
KILL_GRACE_SECONDS = 2.0


def build_environment(
    settings: RunnerSettings,
    workdir: Path,
    configured: Mapping[str, str],
    tool_paths: Sequence[Path] = (),
) -> dict[str, str]:
    """What the server sees: the runner's own variables (above all its token) never leak in.

    Package caches live on the runner's volume so a restart does not download everything again.
    """
    cache = settings.cache_dir
    path = "/usr/local/bin:/usr/bin:/bin"
    if tool_paths:
        path = f"{':'.join(map(str, tool_paths))}:{path}"
    return {
        "PATH": path,
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "LANG": "C.UTF-8",
        "XDG_CACHE_HOME": str(cache / "xdg"),
        "npm_config_cache": str(cache / "npm"),
        "UV_CACHE_DIR": str(cache / "uv"),
        **configured,
    }


class ServerProcess:
    """A started command, read line by line, ended by killing its whole process group."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self._stderr_tail: deque[bytes] = deque()
        self._stderr_size = 0
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    @classmethod
    async def start(
        cls,
        command: str,
        settings: RunnerSettings,
        workdir: Path,
        env: Mapping[str, str],
        tool_paths: Sequence[Path] = (),
    ) -> ServerProcess:
        await anyio.Path(workdir).mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
            env=build_environment(settings, workdir, env, tool_paths),
            limit=MAX_LINE_BYTES,
            # Its own group, so the shell and everything it spawned (npx -> node) end together.
            start_new_session=True,
        )
        return cls(process)

    async def read_line(self) -> bytes | None:
        """The next stdout line, or ``None`` at end of output."""
        assert self._process.stdout is not None
        try:
            line = await self._process.stdout.readline()
        except (asyncio.LimitOverrunError, ValueError):
            return b""
        return line or None

    async def write_line(self, line: str) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(line.encode("utf-8") + b"\n")
        await self._process.stdin.drain()

    async def wait(self) -> int:
        code = await self._process.wait()
        await asyncio.shield(self._stderr_task)
        return code

    @property
    def stderr_tail(self) -> str:
        return b"".join(self._stderr_tail).decode("utf-8", errors="replace").strip()

    async def terminate(self) -> None:
        if self._process.returncode is None:
            self._signal(signal.SIGTERM)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._process.wait(), KILL_GRACE_SECONDS)
        if self._process.returncode is None:
            self._signal(signal.SIGKILL)
            await self._process.wait()
        self._stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._stderr_task

    def _signal(self, sig: signal.Signals) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self._process.pid, sig)

    async def _drain_stderr(self) -> None:
        assert self._process.stderr is not None
        while chunk := await self._process.stderr.read(2048):
            self._stderr_tail.append(chunk)
            self._stderr_size += len(chunk)
            while self._stderr_size > STDERR_TAIL_BYTES and len(self._stderr_tail) > 1:
                self._stderr_size -= len(self._stderr_tail.popleft())
