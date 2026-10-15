"""Exceptions for Sandbox Driver Subsystem."""


class SandboxError(Exception):
    """Base exception for all sandbox-related errors."""


class SandboxNotFoundError(SandboxError):
    """Raised when a referenced sandbox or volume does not exist."""


class SandboxExecutionError(SandboxError):
    """Raised when command execution fails unexpectedly inside a sandbox."""


class SandboxStartError(SandboxExecutionError):
    """Raised when a sandbox cannot start for a reason that waiting will not fix (bad image)."""


class SandboxTimeoutError(SandboxError):
    """Raised when an operation or command execution exceeds allowed timeout.

    Carries what the command printed before it was stopped: the last lines before a hang are
    usually what explains it.
    """

    def __init__(self, message: str, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class SandboxFilesystemError(SandboxError):
    """Raised when a filesystem operation fails inside the sandbox workspace."""
