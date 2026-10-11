"""
Interface definitions for Sandbox Driver Subsystem.
Defines the contract for pluggable execution environments (K8s & Docker).
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

# Tells the idle reaper whether a session still exists; a live session's workspace is never purged.
SessionLivenessCheck = Callable[[str], Awaitable[bool]]


class PtyChannel(ABC):
    """A live pseudo-terminal: raw bytes in, raw bytes out, plus window resizes."""

    @abstractmethod
    async def read(self) -> bytes:
        """The next chunk of terminal output; empty bytes once the shell has exited."""

    @abstractmethod
    async def write(self, data: bytes) -> None:
        """Send keystrokes to the shell."""

    @abstractmethod
    async def resize(self, cols: int, rows: int) -> None:
        """Tell the shell the terminal window changed size."""

    @abstractmethod
    async def close(self) -> None:
        """End the shell and release the connection."""


class SandboxStatus(str, Enum):
    NON_EXISTENT = "non_existent"
    HIBERNATED = "hibernated"  # Volume preserved, zero compute running
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"
    DESTROYED = "destroyed"


@dataclass
class ResourceLimits:
    cpu_cores: float = 2.0
    memory_limit: str = "4Gi"
    disk_size: str = "20Gi"


@dataclass
class WorkspaceSpec:
    session_id: str
    tenant_org_id: str
    tenant_user_id: str
    container_image: str  # e.g. "ghcr.io/org/dev-base:latest"
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    env_vars: dict[str, str] = field(default_factory=dict)
    # Data-egress governance: the org's allowlisted egress
    # destinations (hostnames/CIDRs) and whether link-local/internal ranges are blocked.
    # Empty allowlist means "no org-specific restriction"; internal-subnet blocking is a
    # separate switch so an org can keep public egress while refusing metadata endpoints.
    egress_allowlist: list[str] = field(default_factory=list)
    block_internal_subnets: bool = False
    # Run as uid 1000 with every capability dropped instead of the default root-with-reduced-caps.
    require_non_root: bool = False


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


@dataclass
class FileStat:
    path: str
    is_dir: bool
    size_bytes: int
    modified_at: int


class SandboxDriverProtocol(ABC):
    """
    Contract implemented by both DockerSandboxDriver (local)
    and K8sSandboxDriver (production).
    """

    @abstractmethod
    async def ensure_workspace(self, spec: WorkspaceSpec) -> None:
        """
        Creates the persistent storage (Named Volume in Docker, PVC in K8s)
        if it does not already exist. Idempotent.
        """
        pass

    @abstractmethod
    async def start_sandbox(self, session_id: str) -> None:
        """
        Spins up the ephemeral container or pod mounting the existing workspace storage.
        Transitions state from HIBERNATED to RUNNING.
        """
        pass

    @abstractmethod
    async def hibernate_sandbox(self, session_id: str) -> None:
        """
        Stops/kills the container or pod to scale compute to 0.
        Preserves the persistent volume completely.
        Transitions state from RUNNING to HIBERNATED.
        """
        pass

    def hold_awake(self, session_id: str, seconds: float) -> None:
        """Keep the sandbox out of idle hibernation for ``seconds`` (0 releases the hold).

        Used while background processes run: they are not tool calls, so without a hold the
        idle reaper would stop a dev server the person is still looking at. Drivers without
        an idle reaper keep this no-op.
        """
        return None

    @abstractmethod
    async def destroy_workspace(self, session_id: str) -> None:
        """
        Kills any running compute AND wipes the underlying persistent storage volume.
        """
        pass

    @abstractmethod
    async def get_status(self, session_id: str) -> SandboxStatus:
        """Returns the current lifecycle state of the sandbox."""
        pass

    @abstractmethod
    async def exec_command(
        self,
        session_id: str,
        command: str,
        workdir: str = "/workspace",
        timeout_seconds: int = 120,
        env: dict[str, str] | None = None,
        on_stdout_chunk: Callable[[str], None] | None = None,
    ) -> ExecResult:
        """
        Executes a shell command inside the running sandbox container.
        Captures exit code, stdout, stderr with optional live chunk streaming.
        """
        pass

    @abstractmethod
    async def read_file(
        self,
        session_id: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Reads file contents from the sandbox workspace."""
        pass

    @abstractmethod
    async def write_file(
        self,
        session_id: str,
        path: str,
        content: str,
        overwrite: bool = True,
    ) -> None:
        """Writes or creates a file inside the sandbox workspace."""
        pass

    @abstractmethod
    async def list_files(
        self,
        session_id: str,
        directory: str = "/workspace",
        max_depth: int = 3,
    ) -> list[FileStat]:
        """Lists directory tree structure."""
        pass

    @abstractmethod
    async def apply_patch(
        self,
        session_id: str,
        path: str,
        patch_content: str,
    ) -> bool:
        """
        Applies a unified or AST-diff patch directly to a file in the workspace.
        Returns True if patch applied cleanly.
        """
        pass

    def set_session_env(self, session_id: str, env: dict[str, str]) -> None:  # noqa: B027
        """Set the environment applied to every command of the session (never persisted).

        Used for short-lived credentials; the default is a no-op for drivers that cannot
        honour it.
        """

    async def open_pty(self, session_id: str, cols: int = 80, rows: int = 24) -> "PtyChannel":
        """Open an interactive pseudo-terminal (a login shell in the workspace).

        Optional: drivers that cannot offer one raise ``NotImplementedError``. The caller owns
        the channel and must ``close()`` it.
        """
        raise NotImplementedError("This sandbox driver has no interactive terminal.")
