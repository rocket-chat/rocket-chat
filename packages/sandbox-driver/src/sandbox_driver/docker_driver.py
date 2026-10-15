"""Docker Sandbox Driver implementing SandboxDriverProtocol for local execution."""

import asyncio
import contextlib
import io
import logging
import os
import time
from collections.abc import Callable
from typing import Any

import docker
import docker.errors
import docker.models.containers

from sandbox_driver.blocking import run_blocking
from sandbox_driver.docker_egress import (
    HOST_GATEWAY,
    POLICY_LABEL,
    ensure_egress,
    is_restricted,
    remove_unused_proxies,
)
from sandbox_driver.exceptions import (
    SandboxError,
    SandboxExecutionError,
    SandboxFilesystemError,
    SandboxNotFoundError,
    SandboxTimeoutError,
)
from sandbox_driver.patcher import apply_unified_diff
from sandbox_driver.pty import SHELL_LAUNCHER, DockerPty
from sandbox_driver.session_env import SessionEnvMixin
from sandbox_driver.tracing import traced_exec
from sandbox_driver.utils import (
    CLIENT_DEADLINE_MARGIN_SECONDS,
    SANDBOX_UID_GID,
    BoundedOutput,
    build_list_files_command,
    create_tar_archive,
    extract_file_from_tar,
    hit_deadline,
    normalize_workspace_path,
    parse_list_files_output,
    parse_memory_to_bytes,
    sandbox_dropped_capabilities,
    wrap_with_deadline,
)
from specifications.interfaces.sandbox import (
    ExecResult,
    FileStat,
    PtyChannel,
    SandboxDriverProtocol,
    SandboxStatus,
    SessionLivenessCheck,
    WorkspaceSpec,
)

logger = logging.getLogger(__name__)


class DockerSandboxDriver(SessionEnvMixin, SandboxDriverProtocol):
    """Manages ephemeral Docker containers attached to persistent named volumes."""

    def __init__(
        self,
        client: docker.DockerClient | None = None,
        default_image: str = "python:3.12-slim",
    ) -> None:
        self._client: docker.DockerClient = client or docker.from_env()
        self.default_image: str = default_image
        self._specs: dict[str, WorkspaceSpec] = {}
        self._destroyed_sessions: set[str] = set()
        self.inactivity_pause_seconds: float = float(
            os.getenv("SANDBOX_INACTIVITY_PAUSE_SECONDS", "1800.0")
        )
        self.inactivity_cleanup_seconds: float = float(
            os.getenv("SANDBOX_INACTIVITY_CLEANUP_SECONDS", "0")
        )
        self._last_activity: dict[str, float] = {}
        self._held_until: dict[str, float] = {}
        # Serialises container lifecycle operations per session. Without it, two
        # concurrent turns on the same session can both observe "container absent"
        # and race into `containers.run(name=...)`, where the loser gets a 409.
        self._lifecycle_locks: dict[str, asyncio.Lock] = {}

    def _lifecycle_lock(self, session_id: str) -> asyncio.Lock:
        """Return the per-session lifecycle lock, creating it on first use."""
        lock = self._lifecycle_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._lifecycle_locks[session_id] = lock
        return lock

    def hold_awake(self, session_id: str, seconds: float) -> None:
        if seconds <= 0:
            self._held_until.pop(session_id, None)
        else:
            self._held_until[session_id] = time.time() + seconds

    def record_activity(self, session_id: str) -> None:
        """Mark recent interaction timestamp to reset inactivity countdown."""
        self._last_activity[session_id] = time.time()

    def _get_volume_name(self, session_id: str) -> str:
        return f"sandbox_vol_{session_id}"

    def _get_container_name(self, session_id: str) -> str:
        return f"sandbox_ctr_{session_id}"

    def _sync_get_container(self, session_id: str) -> docker.models.containers.Container | None:
        name = self._get_container_name(session_id)
        try:
            return self._client.containers.get(name)
        except docker.errors.NotFound:
            return None

    async def ensure_workspace(self, spec: WorkspaceSpec) -> None:
        self._specs[spec.session_id] = spec
        self._destroyed_sessions.discard(spec.session_id)
        volume_name = self._get_volume_name(spec.session_id)

        def _sync_create_volume() -> None:
            try:
                self._client.volumes.get(volume_name)
            except docker.errors.NotFound:
                labels = {
                    "rocket.session.id": spec.session_id,
                    "rocket.tenant.org": spec.tenant_org_id,
                    "rocket.tenant.user": spec.tenant_user_id,
                    "rocket.managed": "true",
                }
                self._client.volumes.create(name=volume_name, labels=labels)

        await run_blocking(_sync_create_volume)

    async def start_sandbox(self, session_id: str) -> None:
        spec = self._specs.get(session_id)
        if spec is None:
            spec = WorkspaceSpec(
                session_id=session_id,
                tenant_org_id="default",
                tenant_user_id="default",
                container_image=self.default_image,
            )
            self._specs[session_id] = spec

        await self.ensure_workspace(spec)

        def _sync_start() -> None:
            container_name = self._get_container_name(session_id)
            volume_name = self._get_volume_name(session_id)

            try:
                existing = self._client.containers.get(container_name)
                if existing.status == "running":
                    return
                # Force remove non-running or crashed container to reset state
                existing.remove(force=True)
            except docker.errors.NotFound:
                pass

            try:
                self._client.images.get(spec.container_image)
            except docker.errors.ImageNotFound:
                self._client.images.pull(spec.container_image)

            nano_cpus = int(spec.resources.cpu_cores * 1_000_000_000)
            mem_bytes = parse_memory_to_bytes(spec.resources.memory_limit)
            labels = {
                "rocket.session.id": session_id,
                "rocket.tenant.org": spec.tenant_org_id,
                "rocket.tenant.user": spec.tenant_user_id,
                "rocket.managed": "true",
            }
            environment = dict(spec.env_vars)
            network: str | None = None
            if is_restricted(spec):
                # No route out except through the organization's allow-listing proxy.
                egress = ensure_egress(self._client, spec)
                network = egress.network
                environment.update(egress.environment)
                labels[POLICY_LABEL] = egress.policy_id

            try:
                self._client.containers.run(
                    image=spec.container_image,
                    name=container_name,
                    command=["tail", "-f", "/dev/null"],
                    detach=True,
                    working_dir="/workspace",
                    volumes={volume_name: {"bind": "/workspace", "mode": "rw"}},
                    environment=environment,
                    labels=labels,
                    network=network,
                    extra_hosts=HOST_GATEWAY,
                    nano_cpus=nano_cpus,
                    mem_limit=mem_bytes,
                    pids_limit=256,
                    # A real PID 1 that reaps orphans: processes agents (and MCP servers) start
                    # and stop would otherwise linger as zombies for the life of the container.
                    init=True,
                    cap_drop=sandbox_dropped_capabilities(spec.require_non_root),
                    user=SANDBOX_UID_GID if spec.require_non_root else None,
                    security_opt=["no-new-privileges:true"],
                )
            except docker.errors.APIError as err:
                # A concurrent creator (another worker, or a container left over from a
                # previous process) already holds the name. Reusing it is correct: the
                # name is derived from the session id, and the workspace volume is shared.
                if getattr(err.response, "status_code", None) != 409:
                    raise
                logger.info(
                    "Sandbox container %s already exists; reusing it for session %s",
                    container_name,
                    session_id,
                )

        # Serialise the whole check-then-create sequence: the 409 branch above is the
        # safety net, this lock keeps us from needing it in the first place.
        async with self._lifecycle_lock(session_id):
            await run_blocking(_sync_start)
        self.record_activity(session_id)

    async def hibernate_sandbox(self, session_id: str) -> None:
        def _sync_hibernate() -> None:
            container_name = self._get_container_name(session_id)
            try:
                ctr = self._client.containers.get(container_name)
                # Purge container compute immediately; named volume is preserved intact
                ctr.remove(force=True)
            except docker.errors.NotFound:
                pass

        await run_blocking(_sync_hibernate)

    async def destroy_workspace(self, session_id: str) -> None:
        def _sync_destroy() -> None:
            container_name = self._get_container_name(session_id)
            volume_name = self._get_volume_name(session_id)
            try:
                ctr = self._client.containers.get(container_name)
                ctr.remove(force=True)
            except docker.errors.NotFound:
                pass

            try:
                vol = self._client.volumes.get(volume_name)
                vol.remove(force=True)
            except docker.errors.NotFound:
                pass

        await run_blocking(_sync_destroy)
        self._destroyed_sessions.add(session_id)
        self._specs.pop(session_id, None)

    async def get_status(self, session_id: str) -> SandboxStatus:
        if session_id in self._destroyed_sessions:
            return SandboxStatus.DESTROYED

        def _sync_status() -> SandboxStatus:
            container_name = self._get_container_name(session_id)
            volume_name = self._get_volume_name(session_id)

            try:
                ctr = self._client.containers.get(container_name)
                if ctr.status == "running":
                    return SandboxStatus.RUNNING
                if ctr.status in ("created", "restarting"):
                    return SandboxStatus.STARTING
            except docker.errors.NotFound:
                pass
            except Exception:
                return SandboxStatus.ERROR

            try:
                self._client.volumes.get(volume_name)
                return SandboxStatus.HIBERNATED
            except docker.errors.NotFound:
                return SandboxStatus.NON_EXISTENT
            except Exception:
                return SandboxStatus.ERROR

        return await run_blocking(_sync_status)

    def _sync_stream_exec(
        self,
        container_id: str,
        command: str,
        workdir: str,
        env: dict[str, str] | None,
        on_stdout_chunk: Callable[[str], None] | None,
        stdout_buffer: BoundedOutput,
        stderr_buffer: BoundedOutput,
    ) -> int:
        exec_instance = self._client.api.exec_create(
            container_id,
            cmd=["/bin/sh", "-c", command],
            workdir=workdir,
            environment=env or None,
        )
        exec_id: str = exec_instance["Id"]
        stream = self._client.api.exec_start(exec_id, stream=True, demux=True)

        for stdout_bytes, stderr_bytes in stream:
            if stdout_bytes:
                chunk = stdout_bytes.decode("utf-8", errors="replace")
                stdout_buffer.append(chunk)
                if on_stdout_chunk:
                    on_stdout_chunk(chunk)
            if stderr_bytes:
                chunk = stderr_bytes.decode("utf-8", errors="replace")
                stderr_buffer.append(chunk)

        inspect_data = self._client.api.exec_inspect(exec_id)
        return int(inspect_data.get("ExitCode", 0))

    @traced_exec
    async def exec_command(
        self,
        session_id: str,
        command: str,
        workdir: str = "/workspace",
        timeout_seconds: int = 120,
        env: dict[str, str] | None = None,
        on_stdout_chunk: Callable[[str], None] | None = None,
    ) -> ExecResult:
        self.record_activity(session_id)
        container = await run_blocking(self._sync_get_container, session_id)
        if container is None or container.status != "running":
            raise SandboxNotFoundError(
                f"Active sandbox container for session {session_id} not found."
            )

        if container.id is None:
            raise SandboxExecutionError(f"Container for session {session_id} has invalid ID.")
        container_id: str = container.id

        start_time = time.monotonic()
        # Owned here, not in the worker thread, so a timeout can still report what was printed.
        stdout_buffer, stderr_buffer = BoundedOutput(), BoundedOutput()
        try:
            exit_code = await asyncio.wait_for(
                run_blocking(
                    self._sync_stream_exec,
                    container_id,
                    wrap_with_deadline(command, timeout_seconds),
                    workdir,
                    self.merged_env(session_id, env),
                    on_stdout_chunk,
                    stdout_buffer,
                    stderr_buffer,
                ),
                timeout=timeout_seconds + CLIENT_DEADLINE_MARGIN_SECONDS,
            )
        except TimeoutError as err:
            raise SandboxTimeoutError(
                f"Command timed out after {timeout_seconds} seconds: {command}",
                stdout_buffer.text(),
                stderr_buffer.text(),
            ) from err
        stdout_str, stderr_str = stdout_buffer.text(), stderr_buffer.text()

        if hit_deadline(exit_code, time.monotonic() - start_time, timeout_seconds):
            raise SandboxTimeoutError(
                f"Command timed out after {timeout_seconds} seconds: {command}",
                stdout_str,
                stderr_str,
            )

        duration_ms = int((time.monotonic() - start_time) * 1000)
        return ExecResult(
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            duration_ms=duration_ms,
        )

    async def read_file(
        self,
        session_id: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        self.record_activity(session_id)
        target_path = normalize_workspace_path(path)
        container = await run_blocking(self._sync_get_container, session_id)
        if container is None or container.status != "running":
            raise SandboxNotFoundError(f"Container for session {session_id} is not running.")

        def _sync_read() -> str:
            try:
                bits, _ = container.get_archive(target_path)
                tar_bytes = b"".join(bits)
                return extract_file_from_tar(tar_bytes, os.path.basename(target_path))
            except docker.errors.NotFound as err:
                raise FileNotFoundError(f"File not found in sandbox: {path}") from err
            except Exception as err:
                raise SandboxFilesystemError(f"Failed reading {path}: {err}") from err

        content = await run_blocking(_sync_read)
        if start_line is not None or end_line is not None:
            lines = content.splitlines(keepends=True)
            start_idx = max(0, start_line - 1) if (start_line is not None and start_line > 0) else 0
            end_idx = end_line if end_line is not None else len(lines)
            return "".join(lines[start_idx:end_idx])
        return content

    async def write_file(
        self,
        session_id: str,
        path: str,
        content: str,
        overwrite: bool = True,
    ) -> None:
        self.record_activity(session_id)
        target_path = normalize_workspace_path(path)
        container = await run_blocking(self._sync_get_container, session_id)
        if container is None or container.status != "running":
            raise SandboxNotFoundError(f"Container for session {session_id} is not running.")

        def _stat(target: str) -> tuple[int, int, int] | None:
            """(uid, gid, mode) of ``target`` as the sandbox sees it, or None if missing."""
            code, output = container.exec_run(["stat", "-c", "%u %g %a", target])
            text = output.decode(errors="replace") if isinstance(output, bytes) else ""
            fields = text.split() if code == 0 else []
            if len(fields) != 3 or not all(f.isdigit() for f in fields[:2]):
                return None
            return int(fields[0]), int(fields[1]), int(fields[2], 8)

        def _sync_write() -> None:
            existing = _stat(target_path)
            if existing is not None and not overwrite:
                raise FileExistsError(f"Path already exists and overwrite=False: {path}")
            parent_dir = os.path.dirname(target_path)
            filename = os.path.basename(target_path)
            # Created as the sandbox's own user, so the agent can work in it afterwards.
            container.exec_run(["mkdir", "-p", parent_dir])
            if existing is not None:
                uid, gid, mode = existing  # an edit keeps the file's owner and its +x bit
            else:
                uid, gid, _ = _stat(parent_dir) or (0, 0, 0)
                mode = 0o644
            archive_bytes = create_tar_archive(filename, content.encode("utf-8"), mode, uid, gid)
            container.put_archive(parent_dir, io.BytesIO(archive_bytes))

        await run_blocking(_sync_write)

    async def list_files(
        self,
        session_id: str,
        directory: str = "/workspace",
        max_depth: int = 3,
    ) -> list[FileStat]:
        target_dir = normalize_workspace_path(directory)
        cmd = build_list_files_command(target_dir, max_depth)
        res = await self.exec_command(session_id, cmd)
        if res.exit_code != 0:
            raise SandboxExecutionError(f"Failed to list directory {directory}: {res.stderr}")

        try:
            return parse_list_files_output(res.stdout)
        except ValueError as err:
            raise SandboxFilesystemError(f"Failed parsing file listing: {err}") from err

    async def apply_patch(
        self,
        session_id: str,
        path: str,
        patch_content: str,
    ) -> bool:
        target_path = normalize_workspace_path(path)
        try:
            current_content = await self.read_file(session_id, target_path)
        except (FileNotFoundError, SandboxError):
            return False

        clean, patched_content = apply_unified_diff(current_content, patch_content)
        if not clean:
            return False

        await self.write_file(session_id, target_path, patched_content, overwrite=True)
        return True

    async def open_pty(self, session_id: str, cols: int = 80, rows: int = 24) -> PtyChannel:
        """Start a login shell in the container with a real TTY."""
        self.record_activity(session_id)
        container = await run_blocking(self._sync_get_container, session_id)
        if container is None or container.status != "running":
            raise SandboxNotFoundError(
                f"Active sandbox container for session {session_id} not found."
            )
        container_id = str(container.id or "")

        def _start() -> tuple[str, Any]:
            exec_instance = self._client.api.exec_create(
                container_id,
                cmd=["/bin/sh", "-c", SHELL_LAUNCHER],
                workdir="/workspace",
                environment=self.merged_env(session_id, None) or None,
                stdin=True,
                tty=True,
            )
            exec_id = str(exec_instance["Id"])
            stream = self._client.api.exec_start(exec_id, detach=False, tty=True, socket=True)
            raw = getattr(stream, "_sock", stream)
            if hasattr(raw, "setblocking"):
                raw.setblocking(False)
            # Docker rejects a resize ("timeout waiting for exec session ready") until the exec
            # has started, so the initial size is applied after the start, not before.
            with contextlib.suppress(Exception):
                self._client.api.exec_resize(exec_id, height=rows, width=cols)
            return exec_id, raw

        exec_id, raw_socket = await run_blocking(_start)
        return DockerPty(self._client, exec_id, raw_socket)

    async def adopt_existing_sandboxes(self) -> None:
        """Start idle tracking for managed containers and workspaces created before this process.

        Activity is held in memory, so after an API restart a running container would
        otherwise never be paused and a hibernated workspace (a volume with no container) of a
        session deleted in the meantime would never be reaped. Adopted sandboxes count as
        active *now*: nothing is paused or purged before one full threshold has passed.
        """

        def _list() -> list[str]:
            managed: dict[str, str | list[str] | bool] = {"label": "rocket.managed=true"}
            containers = self._client.containers.list(all=True, filters=managed)
            volumes = self._client.volumes.list(filters=managed)
            return [
                *(c.labels.get("rocket.session.id", "") for c in containers),
                *((v.attrs.get("Labels") or {}).get("rocket.session.id", "") for v in volumes),
            ]

        for session_id in await run_blocking(_list):
            if session_id and session_id not in self._last_activity:
                self.record_activity(session_id)

    async def pause_inactive_sandboxes(self, threshold_seconds: float | None = None) -> list[str]:
        """Hibernate running Docker sandboxes that exceeded configured idle duration."""
        threshold = (
            threshold_seconds if threshold_seconds is not None else self.inactivity_pause_seconds
        )
        now = time.time()
        paused: list[str] = []

        for session_id, last_time in list(self._last_activity.items()):
            if self._held_until.get(session_id, 0) > now:
                continue
            if now - last_time >= threshold:
                status = await self.get_status(session_id)
                if status == SandboxStatus.RUNNING:
                    logger.info(
                        "Pausing inactive Docker sandbox %s (idle for %.1fs, threshold %.1fs)",
                        session_id,
                        now - last_time,
                        threshold,
                    )
                    await self.hibernate_sandbox(session_id)
                    paused.append(session_id)
        return paused

    async def cleanup_inactive_sandboxes(
        self,
        threshold_seconds: float | None = None,
        is_live: SessionLivenessCheck | None = None,
    ) -> list[str]:
        """Purge hibernated workspaces of sessions that no longer exist.

        Disabled unless a positive threshold is configured: the workspace is the session's
        working copy, so it is removed with the session, not on a timer. ``is_live`` guards the
        timer path against destroying work a user can still resume.
        """
        threshold = (
            threshold_seconds if threshold_seconds is not None else self.inactivity_cleanup_seconds
        )
        if threshold <= 0:
            return []
        now = time.time()
        cleaned: list[str] = []

        for session_id, last_time in list(self._last_activity.items()):
            if now - last_time < threshold:
                continue
            if is_live is not None and await is_live(session_id):
                continue
            status = await self.get_status(session_id)
            if status in (SandboxStatus.HIBERNATED, SandboxStatus.ERROR):
                logger.info(
                    "Purging orphaned sandbox workspace %s (idle %.1fs, threshold %.1fs)",
                    session_id,
                    now - last_time,
                    threshold,
                )
                await self.destroy_workspace(session_id)
                cleaned.append(session_id)
                self._last_activity.pop(session_id, None)
        return cleaned

    async def reconcile_inactivity(
        self, is_live: SessionLivenessCheck | None = None
    ) -> tuple[list[str], list[str]]:
        """One sweep: adopt sandboxes that outlived a restart, pause idle compute, purge orphans."""
        await self.adopt_existing_sandboxes()
        paused = await self.pause_inactive_sandboxes()
        cleaned = await self.cleanup_inactive_sandboxes(is_live=is_live)
        await run_blocking(remove_unused_proxies, self._client)
        return paused, cleaned
