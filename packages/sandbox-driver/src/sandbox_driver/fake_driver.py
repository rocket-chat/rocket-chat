"""In-memory sandbox driver for tests, CI and E2E runs.

Selected with ``SANDBOX_DRIVER=fake``. It implements the full
``SandboxDriverProtocol`` with a deterministic, per-session in-memory workspace and
canned command output, so the entire agent/tool surface can be exercised without
Docker, Kubernetes or network access.

Determinism is the point: tests assert on exact stdout, and a turn that touches the
sandbox cannot fail for infrastructure reasons. Commands the fake does not model
return exit code ``127`` with an explicit ``fake driver: unhandled command`` message
instead of pretending to succeed.
"""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import re
import time
from collections.abc import Callable, Mapping

from sandbox_driver.exceptions import SandboxFilesystemError, SandboxNotFoundError
from sandbox_driver.patcher import apply_unified_diff
from sandbox_driver.session_env import SessionEnvMixin
from sandbox_driver.tracing import traced_exec
from specifications.interfaces.sandbox import (
    ExecResult,
    FileStat,
    PtyChannel,
    SandboxDriverProtocol,
    SandboxStatus,
    SessionLivenessCheck,
    WorkspaceSpec,
)

DEFAULT_WORKSPACE_PREFIX = "/workspace"

# Seeded so repo-intelligence and verification tools have something to chew on.
DEFAULT_SEED_FILES: dict[str, str] = {
    "README.md": "# Sample Workspace\n\nSeeded by the fake sandbox driver.\n",
    "src/app.py": "def greet(name: str) -> str:\n    return f'hello {name}'\n",
    "src/util.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
}

# Deterministic canned output for the commands the harness actually runs. Patterns
# are matched in order, so the more specific ones come first.
_CANNED_COMMANDS: tuple[tuple[str, ExecResult], ...] = (
    ("ruff format --check", ExecResult(0, "42 files already formatted\n", "", 5)),
    ("ruff check", ExecResult(0, "All checks passed!\n", "", 5)),
    ("mypy", ExecResult(0, "Success: no issues found in 7 source files\n", "", 5)),
    ("pytest", ExecResult(0, "===== 3 passed in 0.11s =====\n", "", 5)),
    ("npm test", ExecResult(0, "Tests: 5 passed, 5 total\n", "", 5)),
    ("git status", ExecResult(0, "On branch main\nnothing to commit, working tree clean\n", "", 5)),
    ("git rev-parse HEAD", ExecResult(0, "a" * 40 + "\n", "", 5)),
    # The fake workspace is itself one repository (as `git status` above says).
    ("git rev-parse --show-toplevel", ExecResult(0, f"{DEFAULT_WORKSPACE_PREFIX}\n", "", 5)),
    ("rev-parse --git-dir", ExecResult(0, ".git\n", "", 5)),
)


class FakeSandboxDriver(SessionEnvMixin, SandboxDriverProtocol):
    """Deterministic, dependency-free sandbox with an in-memory filesystem."""

    def __init__(self, seed_files: dict[str, str] | None = None) -> None:
        self._files: dict[str, dict[str, str]] = {}
        self._status: dict[str, SandboxStatus] = {}
        self._specs: dict[str, WorkspaceSpec] = {}
        self._scripted: list[tuple[str, ExecResult]] = []
        self._seed = dict(seed_files if seed_files is not None else DEFAULT_SEED_FILES)
        # Audit trail: every command the harness ran, in order.
        self.exec_calls: list[tuple[str, str]] = []
        self.ptys: list[FakePty] = []
        self.exec_envs: list[dict[str, str]] = []
        self._holds: dict[str, dict[str, float]] = {}
        self._held_until: dict[str, float] = {}
        self._last_activity: dict[str, float] = {}
        self.inactivity_pause_seconds: float = 1800.0

    # -- test helpers ---------------------------------------------------------

    def script_command(self, substring: str, result: ExecResult) -> None:
        """Force a specific result for any command containing ``substring``."""
        self._scripted.insert(0, (substring, result))

    def file_snapshot(self, session_id: str) -> dict[str, str]:
        """Return a copy of the session workspace for assertions."""
        return dict(self._files.get(session_id, {}))

    # -- lifecycle ------------------------------------------------------------

    async def ensure_workspace(self, spec: WorkspaceSpec) -> None:
        """Create the in-memory workspace (idempotent) and record the spec."""
        self._specs[spec.session_id] = spec
        self._files.setdefault(spec.session_id, dict(self._seed))
        if self._status.get(spec.session_id) is not SandboxStatus.RUNNING:
            self._status[spec.session_id] = SandboxStatus.HIBERNATED

    async def start_sandbox(self, session_id: str) -> None:
        """Transition the session to RUNNING, creating the workspace if needed."""
        if session_id not in self._specs:
            await self.ensure_workspace(
                WorkspaceSpec(
                    session_id=session_id,
                    tenant_org_id="fake-org",
                    tenant_user_id="fake-user",
                    container_image="fake:latest",
                )
            )
        self._files.setdefault(session_id, dict(self._seed))
        self._status[session_id] = SandboxStatus.RUNNING

    async def hibernate_sandbox(self, session_id: str) -> None:
        """Halt compute but keep the workspace contents."""
        if session_id in self._status:
            self._status[session_id] = SandboxStatus.HIBERNATED

    async def destroy_workspace(self, session_id: str) -> None:
        """Drop compute and storage for the session."""
        self._files.pop(session_id, None)
        self._specs.pop(session_id, None)
        self._status[session_id] = SandboxStatus.DESTROYED

    async def get_status(self, session_id: str) -> SandboxStatus:
        """Return the current lifecycle state."""
        return self._status.get(session_id, SandboxStatus.NON_EXISTENT)

    async def open_pty(self, session_id: str, cols: int = 80, rows: int = 24) -> PtyChannel:
        """A scripted terminal: echoes keystrokes and runs each completed line as a command."""
        if self._status.get(session_id) != SandboxStatus.RUNNING:
            raise SandboxNotFoundError(f"Sandbox for session {session_id} is not running.")
        pty = FakePty(self, session_id, cols, rows)
        self.ptys.append(pty)
        return pty

    def hold_awake(self, session_id: str, seconds: float, reason: str = "process") -> None:
        if seconds <= 0:
            if session_id in self._holds:
                self._holds[session_id].pop(reason, None)
                if not self._holds[session_id]:
                    self._holds.pop(session_id, None)
                    self._held_until.pop(session_id, None)
                else:
                    self._held_until[session_id] = max(self._holds[session_id].values())
            else:
                self._held_until.pop(session_id, None)
        else:
            now = time.time()
            self._holds.setdefault(session_id, {})[reason] = now + seconds
            self._held_until[session_id] = max(self._holds[session_id].values())

    def is_held(self, session_id: str) -> bool:
        """Return True if the sandbox is held awake by an active lease."""
        return self._held_until.get(session_id, 0) > time.time()

    def record_activity(self, session_id: str) -> None:
        self._last_activity[session_id] = time.time()

    async def pause_inactive_sandboxes(
        self,
        threshold_seconds: float | None = None,
        active_sessions: set[str] | None = None,
        session_idleness: Mapping[str, float] | None = None,
    ) -> list[str]:
        threshold = (
            threshold_seconds if threshold_seconds is not None else self.inactivity_pause_seconds
        )
        now = time.time()
        paused: list[str] = []

        activity = dict(self._last_activity)
        if session_idleness:
            for s_id, t_stamp in session_idleness.items():
                if s_id not in activity or t_stamp > activity[s_id]:
                    activity[s_id] = t_stamp

        for session_id, last_time in list(activity.items()):
            if active_sessions and session_id in active_sessions:
                continue
            if self._held_until.get(session_id, 0) > now:
                continue
            if now - last_time >= threshold:
                status = await self.get_status(session_id)
                if status == SandboxStatus.RUNNING:
                    await self.hibernate_sandbox(session_id)
                    paused.append(session_id)
        return paused

    async def reconcile_inactivity(
        self,
        is_live: SessionLivenessCheck | None = None,
        active_sessions: set[str] | None = None,
        session_idleness: Mapping[str, float] | None = None,
    ) -> tuple[list[str], list[str]]:
        paused = await self.pause_inactive_sandboxes(
            active_sessions=active_sessions, session_idleness=session_idleness
        )
        return paused, []

    async def cleanup_warm_pool(self) -> None:
        """No warm pool exists; present for lifecycle parity."""
        return None

    @traced_exec
    async def exec_command(
        self,
        session_id: str,
        command: str,
        workdir: str = DEFAULT_WORKSPACE_PREFIX,
        timeout_seconds: int = 120,
        env: dict[str, str] | None = None,
        on_stdout_chunk: Callable[[str], None] | None = None,
    ) -> ExecResult:
        """Execute a modelled command, returning deterministic output."""
        started = time.perf_counter()
        self.exec_calls.append((session_id, command))
        self.exec_envs.append(self.merged_env(session_id, env))
        files = self._files.setdefault(session_id, dict(self._seed))
        self._status[session_id] = SandboxStatus.RUNNING

        result = self._resolve(command, files)
        if on_stdout_chunk and result.stdout:
            for line in result.stdout.splitlines(keepends=True):
                on_stdout_chunk(line)
        return ExecResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    # -- filesystem -----------------------------------------------------------

    async def read_file(
        self,
        session_id: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Read a workspace file, optionally sliced to a line range."""
        key = _normalise(path)
        content = self._files.setdefault(session_id, dict(self._seed)).get(key)
        if content is None:
            raise SandboxFilesystemError(f"fake driver: no such file: {key}")
        if start_line is None and end_line is None:
            return content
        lines = content.splitlines()
        start = max(1, start_line or 1)
        end = len(lines) if end_line is None else min(end_line, len(lines))
        return "\n".join(lines[start - 1 : end]) + ("\n" if lines else "")

    async def write_file(
        self,
        session_id: str,
        path: str,
        content: str,
        overwrite: bool = True,
    ) -> None:
        """Create or overwrite a workspace file."""
        key = _normalise(path)
        files = self._files.setdefault(session_id, dict(self._seed))
        if key in files and not overwrite:
            raise SandboxFilesystemError(f"fake driver: file exists: {key}")
        files[key] = content

    async def list_files(
        self,
        session_id: str,
        directory: str = DEFAULT_WORKSPACE_PREFIX,
        max_depth: int = 3,
    ) -> list[FileStat]:
        """List workspace entries beneath ``directory`` the way the real drivers do.

        Absolute ``/workspace/...`` paths, directories synthesised from the file keys, depth
        counted from the directory (``find -mindepth 1 -maxdepth``), and, for a path that is a
        file, that file's own entry.
        """
        key = _normalise(directory)
        target = (
            f"{DEFAULT_WORKSPACE_PREFIX}/{key}".rstrip("/") if key else DEFAULT_WORKSPACE_PREFIX
        )
        files = self._files.get(session_id, {})
        if key in files:
            return [
                FileStat(
                    path=target,
                    is_dir=False,
                    size_bytes=len(files[key].encode("utf-8")),
                    modified_at=0,
                )
            ]
        found: dict[str, FileStat] = {}
        for path in sorted(files):
            absolute = f"{DEFAULT_WORKSPACE_PREFIX}/{path}"
            if not absolute.startswith(f"{target}/"):
                continue
            parts = absolute[len(target) + 1 :].split("/")
            for depth in range(1, min(len(parts), max_depth) + 1):
                entry = f"{target}/{'/'.join(parts[:depth])}"
                is_file = depth == len(parts)
                found.setdefault(
                    entry,
                    FileStat(
                        path=entry,
                        is_dir=not is_file,
                        size_bytes=len(files[path].encode("utf-8")) if is_file else 0,
                        modified_at=0,
                    ),
                )
        return [found[path] for path in sorted(found)]

    async def apply_patch(self, session_id: str, path: str, patch_content: str) -> bool:
        """Apply a unified diff, reusing the driver-neutral patch engine."""
        key = _normalise(path)
        files = self._files.setdefault(session_id, dict(self._seed))
        if key not in files:
            return False
        applied, patched = apply_unified_diff(files[key], patch_content)
        if applied:
            files[key] = patched
        return applied

    # -- command resolution ---------------------------------------------------

    def _resolve(self, command: str, files: dict[str, str]) -> ExecResult:
        """Map a shell command onto deterministic output."""
        for substring, result in self._scripted:
            if substring in command:
                return result
        for substring, result in _CANNED_COMMANDS:
            if substring in command:
                return result
        for handler in (
            _handle_echo,
            _handle_cat,
            _handle_stat_size,
            _handle_dd_chunk,
            _handle_find,
            _handle_ls,
            _handle_pwd,
        ):
            resolved = handler(command, files)
            if resolved is not None:
                return resolved
        return ExecResult(127, "", f"fake driver: unhandled command: {command}\n", 0)


def _normalise(path: str) -> str:
    """Reduce any workspace path form to a bare, relative key."""
    trimmed = path.strip().strip("'\"")
    for prefix in ("/workspace/", "/workspace", "./"):
        if trimmed.startswith(prefix):
            trimmed = trimmed[len(prefix) :]
    return trimmed.strip("/")


def _handle_pwd(command: str, files: dict[str, str]) -> ExecResult | None:
    if command == "pwd":
        return ExecResult(0, f"{DEFAULT_WORKSPACE_PREFIX}\n", "", 0)
    return None


def _handle_echo(command: str, files: dict[str, str]) -> ExecResult | None:
    """Support both ``echo <text>`` and ``echo <text> > path`` writes."""
    if not command.startswith("echo "):
        return None
    argument = command[len("echo ") :].strip()
    if " > " in argument:
        content, _, target = argument.partition(" > ")
        files[_normalise(target)] = content.strip().strip("'\"") + "\n"
        return ExecResult(0, "", "", 0)
    text = argument.strip().strip("'\"")
    return ExecResult(0, f"{text}\n", "", 0)


def _handle_cat(command: str, files: dict[str, str]) -> ExecResult | None:
    if not command.startswith("cat "):
        return None
    target = _normalise(command[len("cat ") :].split()[0])
    if target not in files:
        return ExecResult(1, "", f"cat: {target}: No such file or directory\n", 0)
    return ExecResult(0, files[target], "", 0)


def _handle_stat_size(command: str, files: dict[str, str]) -> ExecResult | None:
    """``stat -c %s <path>``: the byte size ``sandbox_driver.blobs`` asks for first."""
    match = re.match(r"stat -c %s '?([^'\s]+)'?$", command.strip())
    if match is None:
        return None
    target = _normalise(match.group(1))
    if target not in files:
        return ExecResult(1, "", f"stat: cannot stat '{target}'\n", 0)
    return ExecResult(0, f"{len(files[target].encode('utf-8'))}\n", "", 0)


def _handle_dd_chunk(command: str, files: dict[str, str]) -> ExecResult | None:
    """``dd if=<path> bs=N skip=K count=1 | base64 -w0``: one base64 chunk of a file."""
    match = re.match(
        r"dd if='?([^'\s]+)'? bs=(\d+) skip=(\d+) count=1 .*base64 -w0$", command.strip()
    )
    if match is None:
        return None
    target = _normalise(match.group(1))
    if target not in files:
        return ExecResult(1, "", f"dd: failed to open '{target}'\n", 0)
    size, skip = int(match.group(2)), int(match.group(3))
    chunk = files[target].encode("utf-8")[skip * size : (skip + 1) * size]
    return ExecResult(0, base64.b64encode(chunk).decode(), "", 0)


def _handle_ls(command: str, files: dict[str, str]) -> ExecResult | None:
    if not (command == "ls" or command.startswith(("ls ", "ls -"))):
        return None
    parts = [part for part in command.split() if not part.startswith("-")]
    prefix = _normalise(parts[1]) if len(parts) > 1 else ""
    entries: set[str] = set()
    for path in files:
        if prefix and not path.startswith(prefix):
            continue
        remainder = path[len(prefix) :].lstrip("/") if prefix else path
        if not remainder:
            continue
        entries.add(remainder.split("/")[0])
    return ExecResult(0, "".join(f"{entry}\n" for entry in sorted(entries)), "", 0)


def _handle_find(command: str, files: dict[str, str]) -> ExecResult | None:
    """Model ``find /workspace ...`` for the repo-intelligence tools."""
    if not command.startswith("find "):
        return None
    tokens = command.split()
    name_filter = ""
    if "-name" in tokens:
        index = tokens.index("-name")
        if index + 1 < len(tokens):
            name_filter = tokens[index + 1].strip("'\"")

    matches = [
        path for path in sorted(files) if not name_filter or fnmatch.fnmatch(path, name_filter)
    ]
    lines = "".join(f"{DEFAULT_WORKSPACE_PREFIX}/{path}\n" for path in matches)
    return ExecResult(0, lines, "", 0)


class FakePty(PtyChannel):
    """Deterministic terminal for tests: line-buffered, one command per line."""

    PROMPT = b"$ "

    def __init__(self, driver: FakeSandboxDriver, session_id: str, cols: int, rows: int) -> None:
        self._driver = driver
        self._session_id = session_id
        self.size = (cols, rows)
        self.closed = False
        self._line = ""
        self._output: asyncio.Queue[bytes] = asyncio.Queue()
        self._output.put_nowait(self.PROMPT)

    async def read(self) -> bytes:
        return await self._output.get()

    async def write(self, data: bytes) -> None:
        for char in data.decode("utf-8", errors="replace"):
            if char in "\r\n":
                command, self._line = self._line.strip(), ""
                self._output.put_nowait(b"\r\n")
                if command == "exit":
                    self._output.put_nowait(b"")
                    return
                if command:
                    result = await self._driver.exec_command(self._session_id, command)
                    text = (result.stdout + result.stderr).replace("\n", "\r\n")
                    self._output.put_nowait(text.encode())
                self._output.put_nowait(self.PROMPT)
            else:
                self._line += char
                self._output.put_nowait(char.encode())

    async def resize(self, cols: int, rows: int) -> None:
        self.size = (cols, rows)

    async def close(self) -> None:
        self.closed = True
        self._output.put_nowait(b"")
