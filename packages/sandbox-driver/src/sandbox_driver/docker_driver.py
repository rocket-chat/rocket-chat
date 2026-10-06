"""Docker Sandbox Driver implementing SandboxDriverProtocol for local execution."""

import asyncio
import base64
import io
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable

import docker
import docker.errors
import docker.models.containers

from sandbox_driver.exceptions import (
    SandboxError,
    SandboxExecutionError,
    SandboxFilesystemError,
    SandboxNotFoundError,
    SandboxTimeoutError,
)
from sandbox_driver.patcher import apply_unified_diff
from sandbox_driver.utils import (
    create_tar_archive,
    extract_file_from_tar,
    normalize_workspace_path,
    parse_memory_to_bytes,
)
from specifications.interfaces.sandbox import (
    ExecResult,
    FileStat,
    SandboxDriverProtocol,
    SandboxStatus,
    WorkspaceSpec,
)

logger = logging.getLogger(__name__)


class DockerSandboxDriver(SandboxDriverProtocol):
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

        await asyncio.to_thread(_sync_create_volume)

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

            self._client.containers.run(
                image=spec.container_image,
                name=container_name,
                command=["tail", "-f", "/dev/null"],
                detach=True,
                working_dir="/workspace",
                volumes={volume_name: {"bind": "/workspace", "mode": "rw"}},
                environment=spec.env_vars,
                labels=labels,
                nano_cpus=nano_cpus,
                mem_limit=mem_bytes,
            )

        await asyncio.to_thread(_sync_start)

    async def hibernate_sandbox(self, session_id: str) -> None:
        def _sync_hibernate() -> None:
            container_name = self._get_container_name(session_id)
            try:
                ctr = self._client.containers.get(container_name)
                # Purge container compute immediately; named volume is preserved intact
                ctr.remove(force=True)
            except docker.errors.NotFound:
                pass

        await asyncio.to_thread(_sync_hibernate)

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

        await asyncio.to_thread(_sync_destroy)
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

        return await asyncio.to_thread(_sync_status)

    def _sync_stream_exec(
        self,
        container_id: str,
        command: str,
        workdir: str,
        env: dict[str, str] | None,
        on_stdout_chunk: Callable[[str], None] | None,
    ) -> tuple[int, str, str]:
        exec_instance = self._client.api.exec_create(
            container_id,
            cmd=["/bin/sh", "-c", command],
            workdir=workdir,
            environment=env,
        )
        exec_id: str = exec_instance["Id"]
        stream = self._client.api.exec_start(exec_id, stream=True, demux=True)

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        for stdout_bytes, stderr_bytes in stream:
            if stdout_bytes:
                chunk = stdout_bytes.decode("utf-8", errors="replace")
                stdout_chunks.append(chunk)
                if on_stdout_chunk:
                    on_stdout_chunk(chunk)
            if stderr_bytes:
                chunk = stderr_bytes.decode("utf-8", errors="replace")
                stderr_chunks.append(chunk)

        inspect_data = self._client.api.exec_inspect(exec_id)
        exit_code = int(inspect_data.get("ExitCode", 0))
        return exit_code, "".join(stdout_chunks), "".join(stderr_chunks)

    async def exec_command(
        self,
        session_id: str,
        command: str,
        workdir: str = "/workspace",
        timeout_seconds: int = 120,
        env: dict[str, str] | None = None,
        on_stdout_chunk: Callable[[str], None] | None = None,
    ) -> ExecResult:
        container = await asyncio.to_thread(self._sync_get_container, session_id)
        if container is None or container.status != "running":
            raise SandboxNotFoundError(
                f"Active sandbox container for session {session_id} not found."
            )

        if container.id is None:
            raise SandboxExecutionError(f"Container for session {session_id} has invalid ID.")
        container_id: str = container.id

        start_time = time.monotonic()
        try:
            exit_code, stdout_str, stderr_str = await asyncio.wait_for(
                asyncio.to_thread(
                    self._sync_stream_exec,
                    container_id,
                    command,
                    workdir,
                    env,
                    on_stdout_chunk,
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError as err:
            raise SandboxTimeoutError(
                f"Command timed out after {timeout_seconds} seconds: {command}"
            ) from err

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
        target_path = normalize_workspace_path(path)
        container = await asyncio.to_thread(self._sync_get_container, session_id)
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

        content = await asyncio.to_thread(_sync_read)
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
        target_path = normalize_workspace_path(path)
        container = await asyncio.to_thread(self._sync_get_container, session_id)
        if container is None or container.status != "running":
            raise SandboxNotFoundError(f"Container for session {session_id} is not running.")

        def _sync_write() -> None:
            if not overwrite:
                try:
                    container.get_archive(target_path)
                    raise FileExistsError(f"Path already exists and overwrite=False: {path}")
                except docker.errors.NotFound:
                    pass

            parent_dir = os.path.dirname(target_path)
            filename = os.path.basename(target_path)
            # Create parent directories before archiving
            container.exec_run(["mkdir", "-p", parent_dir])
            archive_bytes = create_tar_archive(filename, content.encode("utf-8"))
            container.put_archive(parent_dir, io.BytesIO(archive_bytes))

        await asyncio.to_thread(_sync_write)

    def _build_list_files_command(self, target_dir: str, max_depth: int) -> str:
        script = (
            "import json, os, sys\n"
            f"root = {json.dumps(target_dir)}\n"
            f"max_depth = {max_depth}\n"
            "results = []\n"
            "if os.path.exists(root):\n"
            "    if not os.path.isdir(root):\n"
            "        st = os.stat(root)\n"
            "        results.append({'path': root, 'is_dir': False, 'size_bytes': st.st_size, 'modified_at': int(st.st_mtime)})\n"
            "    else:\n"
            "        for dirpath, dirnames, filenames in os.walk(root):\n"
            "            rel = os.path.relpath(dirpath, root)\n"
            "            depth = 0 if rel == '.' else rel.count(os.sep) + 1\n"
            "            if depth >= max_depth:\n"
            "                dirnames.clear()\n"
            "            for d in sorted(dirnames):\n"
            "                full_p = os.path.join(dirpath, d)\n"
            "                try:\n"
            "                    st = os.stat(full_p)\n"
            "                    results.append({'path': full_p, 'is_dir': True, 'size_bytes': st.st_size, 'modified_at': int(st.st_mtime)})\n"
            "                except OSError:\n"
            "                    pass\n"
            "            for f in sorted(filenames):\n"
            "                full_p = os.path.join(dirpath, f)\n"
            "                try:\n"
            "                    st = os.stat(full_p)\n"
            "                    results.append({'path': full_p, 'is_dir': False, 'size_bytes': st.st_size, 'modified_at': int(st.st_mtime)})\n"
            "                except OSError:\n"
            "                    pass\n"
            "print(json.dumps(results))\n"
        )
        b64_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
        # Base64 bypasses shell escaping quirks across different shells
        return f"python3 -c \"import base64; exec(base64.b64decode('{b64_script}'))\""

    async def list_files(
        self,
        session_id: str,
        directory: str = "/workspace",
        max_depth: int = 3,
    ) -> list[FileStat]:
        target_dir = normalize_workspace_path(directory)
        cmd = self._build_list_files_command(target_dir, max_depth)
        res = await self.exec_command(session_id, cmd)
        if res.exit_code != 0:
            raise SandboxExecutionError(f"Failed to list directory {directory}: {res.stderr}")

        try:
            raw_entries = json.loads(res.stdout)
            return [
                FileStat(
                    path=item["path"],
                    is_dir=bool(item["is_dir"]),
                    size_bytes=int(item["size_bytes"]),
                    modified_at=int(item["modified_at"]),
                )
                for item in raw_entries
            ]
        except Exception as err:
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

    async def attach_pty(
        self,
        session_id: str,
        cols: int = 80,
        rows: int = 24,
    ) -> AsyncIterator[bytes]:
        """Attach an interactive bidirectional pseudo-terminal stream to the container."""
        container = await asyncio.to_thread(self._sync_get_container, session_id)
        if container is None or container.status != "running":
            raise SandboxNotFoundError(
                f"Active sandbox container for session {session_id} not found."
            )

        container_id: str = str(container.id or "")

        # Create exec instance with pty (tty=True, stdin=True)
        exec_instance = self._client.api.exec_create(
            container_id,
            cmd=["/bin/sh"],
            workdir="/workspace",
            stdin=True,
            tty=True,
        )
        exec_id = exec_instance["Id"]

        # Resize tty
        try:
            self._client.api.exec_resize(exec_id, height=rows, width=cols)
        except Exception as resize_err:
            logger.debug("exec_resize skipped: %s", resize_err)

        # Start streaming
        socket = self._client.api.exec_start(
            exec_id, detach=False, tty=True, stream=True, socket=True
        )
        raw_socket = getattr(socket, "_sock", socket)
        if hasattr(raw_socket, "setblocking"):
            raw_socket.setblocking(False)

        loop = asyncio.get_running_loop()
        while True:
            try:
                chunk = await loop.sock_recv(raw_socket, 4096)  # type: ignore[arg-type]
                if not chunk:
                    break
                yield chunk
            except (asyncio.CancelledError, GeneratorExit):
                break
            except Exception as read_err:
                logger.debug("PTY read finished or closed: %s", read_err)
                break
