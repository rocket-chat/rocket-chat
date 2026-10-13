"""Installing the program a command server runs (a release binary, for example) once, and keeping it.

A server that is not an ``npx``/``uvx`` package needs its executable on the runner. The
administrator gives an install script; it runs once per organization and script, in a clean
environment *without* the server's credentials (the result is shared by every member's
process), inside a directory on the runner's volume that is then put on the server's ``PATH``.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import signal
from pathlib import Path

import anyio

from mcp_runner.process import build_environment
from mcp_runner.settings import RunnerSettings

SETUP_TIMEOUT_SECONDS = 600.0
OUTPUT_TAIL_CHARS = 2000
_READY = ".ready"


def executable_directories(root: Path) -> list[Path]:
    """Where an unpacked release keeps its programs: the root, ``bin``, and the same one folder down.

    Release archives differ (``tool`` at the top, ``bin/tool``, or ``tool_linux_amd64/bin/tool``),
    and the administrator should not have to know which one the command needs.
    """
    candidates = [root]
    candidates += [child for child in sorted(root.iterdir()) if child.is_dir()]
    found: dict[Path, None] = {}
    for directory in candidates:
        found[directory] = None
        if (directory / "bin").is_dir():
            found[directory / "bin"] = None
    return list(found)


class SetupFailed(RuntimeError):
    """The install script did not finish successfully (the message carries its output)."""


def tool_directory(settings: RunnerSettings, org_id: str, setup: str) -> Path:
    digest = hashlib.sha256(setup.encode()).hexdigest()[:16]
    return settings.cache_dir / "tools" / org_id / digest


class ToolInstaller:
    """Runs install scripts, one at a time per directory, and remembers which succeeded."""

    def __init__(self, settings: RunnerSettings) -> None:
        self._settings = settings
        self._locks: dict[Path, asyncio.Lock] = {}

    async def ensure(self, org_id: str, setup: str) -> Path:
        directory = tool_directory(self._settings, org_id, setup)
        async with self._locks.setdefault(directory, asyncio.Lock()):
            marker = anyio.Path(directory / _READY)
            if await marker.exists():
                return directory
            await anyio.Path(directory).mkdir(parents=True, exist_ok=True)
            await self._run(setup, directory)
            await marker.touch()
        return directory

    async def _run(self, setup: str, directory: Path) -> None:
        process = await asyncio.create_subprocess_exec(
            "sh",
            "-ec",
            setup,
            cwd=directory,
            env={
                **build_environment(self._settings, directory, {}),
                "MCP_TOOL_DIR": str(directory),
            },
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), SETUP_TIMEOUT_SECONDS)
        except TimeoutError as err:
            self._kill(process)
            raise SetupFailed(
                f"The install command took longer than {SETUP_TIMEOUT_SECONDS:g}s."
            ) from err
        if process.returncode != 0:
            tail = output.decode("utf-8", errors="replace").strip()[-OUTPUT_TAIL_CHARS:]
            raise SetupFailed(f"The install command failed ({process.returncode}).\n{tail}")

    @staticmethod
    def _kill(process: asyncio.subprocess.Process) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
