"""Exceptions for Sandbox Driver Subsystem."""


class SandboxError(Exception):
    """Base exception for all sandbox-related errors."""


class SandboxNotFoundError(SandboxError):
    """Raised when a referenced sandbox or volume does not exist."""


class SandboxExecutionError(SandboxError):
    """Raised when command execution fails unexpectedly inside a sandbox."""


class SandboxTimeoutError(SandboxError):
    """Raised when an operation or command execution exceeds allowed timeout."""


class SandboxFilesystemError(SandboxError):
    """Raised when a filesystem operation fails inside the sandbox workspace."""
