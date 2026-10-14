"""A sandbox driver that runs commands in a real directory, for tests that need real git."""

from __future__ import annotations

import asyncio
import os
import pathlib
import stat
from typing import Any

from specifications.interfaces.sandbox import ExecResult

# ``base64 -w0`` is what the Debian sandbox runs; this makes it work on any developer machine.
_BASE64_SHIM = (
    "#!/bin/sh\n"
    "exec python3 -c 'import sys,base64\n"
    "data=sys.stdin.buffer.read()\n"
    'if "-d" in sys.argv[1:]:\n'
    "    sys.stdout.buffer.write(base64.b64decode(data))\n"
    "else:\n"
    '    sys.stdout.write(base64.b64encode(data).decode())\' "$@"\n'
)


class LocalGitDriver:
    """Just enough of the sandbox driver: ``exec_command`` in a working directory.

    ``workdir`` stands for ``/workspace``: that path and anything under it map into it.
    """

    def __init__(self, workdir: pathlib.Path, shim_dir: pathlib.Path) -> None:
        self.workdir = workdir
        shim_dir.mkdir(parents=True, exist_ok=True)
        shim = shim_dir / "base64"
        shim.write_text(_BASE64_SHIM)
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        self._path = f"{shim_dir}:{os.environ['PATH']}"
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.session_env: dict[str, str] = {}
        self.home: pathlib.Path | None = None

    def set_session_env(self, session_id: str, env: dict[str, str]) -> None:
        self.session_env = dict(env)

    async def exec_command(
        self,
        session_id: str,
        command: str,
        workdir: str = "",
        timeout_seconds: int = 120,
        env: dict[str, str] | None = None,
        on_stdout_chunk: Any = None,
    ) -> ExecResult:
        # Commands name /workspace literally (clone_repository does); it is this directory here.
        command = command.replace("/workspace", str(self.workdir))
        self.calls.append((command, dict(env or {})))
        cwd = self.workdir
        if workdir.startswith("/workspace/"):
            cwd = self.workdir / workdir.removeprefix("/workspace/")
        elif workdir and workdir != "/workspace":
            cwd = pathlib.Path(workdir)
        environment = {
            **os.environ,
            "PATH": self._path,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            **({"HOME": str(self.home)} if self.home else {}),
            **self.session_env,
            **(env or {}),
        }
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            env=environment,
            executable="/bin/bash",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
        return ExecResult(
            exit_code=process.returncode or 0,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            duration_ms=0,
        )
