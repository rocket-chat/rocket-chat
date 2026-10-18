"""Background processes in the sandbox: dev servers, watchers, long builds.

``bash_exec`` blocks until its command ends, so anything that keeps running (a dev server, a
test watcher) either times out or is killed with the call. These helpers start a command in
its own session, keep its output in a capped log, and let the agent list, read, wait for and
stop it later - the same model modern coding harnesses use.

Each process lives in ``/tmp/rocket/proc/<id>/``::

    cmd     the command line, for display
    meta    ``name`` and optional ``port``, one per line
    pid     the process group leader
    out.log combined stdout and stderr, capped by ``ulimit -f``
    exit    the exit code, written by the wrapper when the command ends
    stopped present when the agent (or a person) stopped it

State is derived from those files, so it survives an API restart and needs no daemon.
"""

from __future__ import annotations

import asyncio
import re
import shlex
import uuid
from dataclasses import dataclass
from typing import Literal

from agent_core.tools.clamp import clamp_output
from specifications.interfaces.sandbox import SandboxDriverProtocol

PROC_ROOT = "/tmp/rocket/proc"
LOG_LIMIT_BLOCKS = 20480  # ulimit -f counts 512-byte blocks: a 10 MiB log
MAX_WAIT_SECONDS = 300
DEFAULT_TAIL_LINES = 100
MAX_TAIL_LINES = 500
STOP_GRACE_SECONDS = 3

ProcessState = Literal["running", "exited", "stopped", "lost"]
_STATES: dict[str, ProcessState] = {"running": "running", "exited": "exited", "stopped": "stopped"}
_ID = re.compile(r"^p[0-9a-f]{6}$")
_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9 ._:/@+-]")


@dataclass(frozen=True)
class ProcessInfo:
    id: str
    state: ProcessState
    exit_code: int | None
    port: int | None
    name: str
    command: str

    @property
    def running(self) -> bool:
        return self.state == "running"

    def summary(self) -> str:
        detail = {
            "running": "running",
            "exited": f"exited with code {self.exit_code}",
            "stopped": "stopped",
            "lost": "lost (the sandbox restarted)",
        }[self.state]
        port = f" port {self.port}" if self.port else ""
        return f"{self.id} [{detail}]{port} {self.name}: {self.command}"


def is_valid_id(process_id: str) -> bool:
    return bool(_ID.match(process_id))


def _clean_name(name: str | None, command: str) -> str:
    base = _NAME_UNSAFE.sub(
        "", (name or command).strip().splitlines()[0] if (name or command) else ""
    )
    return base[:60].strip() or "process"


_WRAPPER = (
    "import subprocess, sys\n"
    "cmd, out_file, exit_file = sys.argv[1], sys.argv[2], sys.argv[3]\n"
    "max_bytes = 10 * 1024 * 1024\n"
    "p = subprocess.Popen(cmd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)\n"
    "with open(out_file, 'wb') as f:\n"
    "    if p.stdout:\n"
    "        for line in p.stdout:\n"
    "            f.write(line)\n"
    "            f.flush()\n"
    "            if f.tell() > max_bytes:\n"
    "                f.seek(max_bytes // 2)\n"
    "                rem = f.read()\n"
    "                f.seek(0)\n"
    "                f.truncate(0)\n"
    "                f.write(rem)\n"
    "                f.flush()\n"
    "rc = p.wait()\n"
    "with open(exit_file, 'w') as f:\n"
    "    f.write(str(rc) + '\\n')\n"
)

_START = """\
set -e
D={dir}
mkdir -p "$D"
printf %s {command} > "$D/cmd"
printf 'name=%s\\n' {name} > "$D/meta"
{port_line}
if command -v setsid >/dev/null 2>&1; then
  setsid python3 -c {wrapper_code} {command} "$D/out.log" "$D/exit" </dev/null >/dev/null 2>&1 &
else
  # No setsid (rare): job control still gives the command its own process group.
  set -m
  nohup python3 -c {wrapper_code} {command} "$D/out.log" "$D/exit" </dev/null >/dev/null 2>&1 &
fi
echo $! > "$D/pid"
echo started
"""

_STATUS = """\
for D in {root}/*/; do
  [ -f "$D/cmd" ] || continue
  ID=$(basename "$D")
  PID=$(cat "$D/pid" 2>/dev/null)
  if [ -f "$D/exit" ]; then S=exited; C=$(cat "$D/exit")
  elif [ -f "$D/stopped" ]; then S=stopped; C=
  elif [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then S=running; C=
  else S=lost; C=
  fi
  PORT=$(sed -n 's/^port=//p' "$D/meta" 2>/dev/null)
  NAME=$(sed -n 's/^name=//p' "$D/meta" 2>/dev/null)
  printf 'ROCKET_PROC\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' "$ID" "$S" "$C" "$PORT" "$NAME" "$(head -c 300 "$D/cmd")"
done
"""


async def start_process(
    driver: SandboxDriverProtocol,
    session_id: str,
    command: str,
    *,
    name: str | None = None,
    port: int | None = None,
    workdir: str = "/workspace",
    max_running: int = 5,
) -> str:
    """Start ``command`` in the background and return a message for the agent."""
    if not command.strip():
        return "Error: command is empty."
    if port is not None and not 1 <= port <= 65535:
        return "Error: port must be between 1 and 65535."
    running = [p for p in await list_processes(driver, session_id) if p.running]
    if len(running) >= max_running:
        return (
            f"Error: {len(running)} background processes are already running (limit "
            f"{max_running}). Stop one with process_stop first:\n"
            + "\n".join(p.summary() for p in running)
        )
    process_id = f"p{uuid.uuid4().hex[:6]}"
    directory = f"{PROC_ROOT}/{process_id}"
    script = _START.format(
        dir=shlex.quote(directory),
        command=shlex.quote(command),
        name=shlex.quote(_clean_name(name, command)),
        port_line=f"printf 'port=%s\\n' {int(port)} >> \"$D/meta\"" if port else ":",
        wrapper_code=shlex.quote(_WRAPPER),
    )
    result = await driver.exec_command(session_id, script, workdir=workdir, timeout_seconds=30)
    if result.exit_code != 0 or "started" not in result.stdout:
        detail = (result.stderr or result.stdout).strip()
        return f"Error: could not start the process: {clamp_output(detail, 500)}"
    await asyncio.sleep(0.5)
    info = await get_process(driver, session_id, process_id)
    if info is not None and not info.running:
        tail = await read_output(driver, session_id, process_id, 40)
        return f"The process ended immediately ({info.summary()}). Output:\n{tail}"
    note = f" Declared port {port}: open it in the Preview tab once it listens." if port else ""
    return (
        f"Started {process_id} in the background.{note} Read its output with process_output, "
        f"wait for it with process_wait, stop it with process_stop."
    )


async def list_processes(driver: SandboxDriverProtocol, session_id: str) -> list[ProcessInfo]:
    script = _STATUS.format(root=PROC_ROOT)
    result = await driver.exec_command(session_id, script, timeout_seconds=20)
    return _parse(result.stdout)


async def get_process(
    driver: SandboxDriverProtocol, session_id: str, process_id: str
) -> ProcessInfo | None:
    return next((p for p in await list_processes(driver, session_id) if p.id == process_id), None)


def _parse(output: str) -> list[ProcessInfo]:
    processes: list[ProcessInfo] = []
    for line in output.splitlines():
        if not line.startswith("ROCKET_PROC\t"):
            continue
        parts = line.split("\t", 6)
        if len(parts) < 7:
            continue
        _, process_id, state, code, port, name, command = parts
        processes.append(
            ProcessInfo(
                id=process_id,
                state=_STATES.get(state, "lost"),
                exit_code=int(code) if code.strip().lstrip("-").isdigit() else None,
                port=int(port) if port.isdigit() else None,
                name=name or "process",
                command=command,
            )
        )
    return processes


async def read_output(
    driver: SandboxDriverProtocol, session_id: str, process_id: str, lines: int = DEFAULT_TAIL_LINES
) -> str:
    if not is_valid_id(process_id):
        return f"Error: '{process_id}' is not a process id."
    count = max(1, min(lines, MAX_TAIL_LINES))
    log = f"{PROC_ROOT}/{process_id}/out.log"
    result = await driver.exec_command(
        session_id, f"tail -n {count} {shlex.quote(log)} 2>&1", timeout_seconds=15
    )
    if result.exit_code != 0:
        return f"Error: no output for {process_id} ({(result.stdout or result.stderr).strip()})."
    return clamp_output(result.stdout.strip() or "(no output yet)", 6000)


async def wait_for_process(
    driver: SandboxDriverProtocol,
    session_id: str,
    process_id: str,
    timeout_seconds: int = 60,
    poll_seconds: float = 1.0,
) -> str:
    if not is_valid_id(process_id):
        return f"Error: '{process_id}' is not a process id."
    deadline = asyncio.get_running_loop().time() + max(1, min(timeout_seconds, MAX_WAIT_SECONDS))
    while True:
        info = await get_process(driver, session_id, process_id)
        if info is None:
            return f"Error: no process {process_id}."
        if not info.running:
            tail = await read_output(driver, session_id, process_id, 40)
            return f"{info.summary()}\nLast output:\n{tail}"
        if asyncio.get_running_loop().time() >= deadline:
            return f"{info.summary()} - still running after {timeout_seconds}s."
        await asyncio.sleep(poll_seconds)


async def stop_process(driver: SandboxDriverProtocol, session_id: str, process_id: str) -> str:
    """Terminate the whole process group: SIGTERM, a short grace, then SIGKILL."""
    if not is_valid_id(process_id):
        return f"Error: '{process_id}' is not a process id."
    directory = shlex.quote(f"{PROC_ROOT}/{process_id}")
    script = (
        f'D={directory}; [ -f "$D/cmd" ] || {{ echo missing; exit 3; }}; '
        'PID=$(cat "$D/pid" 2>/dev/null); '
        'if [ -n "$PID" ]; then '
        'kill -TERM "-$PID" 2>/dev/null || kill -TERM "$PID" 2>/dev/null; '
        f"sleep {STOP_GRACE_SECONDS}; "
        'kill -KILL "-$PID" 2>/dev/null || kill -KILL "$PID" 2>/dev/null; fi; '
        'touch "$D/stopped"; echo done'
    )
    result = await driver.exec_command(session_id, script, timeout_seconds=30)
    if "missing" in result.stdout:
        return f"Error: no process {process_id}."
    info = await get_process(driver, session_id, process_id)
    return f"Stopped {process_id}." if info is None else f"{info.summary()}"
