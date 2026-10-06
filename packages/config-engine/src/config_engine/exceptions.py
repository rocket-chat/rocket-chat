"""Exception definitions for the Config Engine."""


class ConfigEngineError(Exception):
    """Base exception for all configuration engine errors."""


class PolicyViolationError(ConfigEngineError):
    """Raised when a user or team override violates organization policy constraints."""


class CredentialDecryptionError(ConfigEngineError):
    """Raised when encrypted BYOK credentials cannot be decrypted."""
