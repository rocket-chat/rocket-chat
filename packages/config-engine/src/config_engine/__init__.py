"""Hierarchical Configuration & Policy Engine for Rocket Chat."""

from .cascading import (
    CascadingSettingsResolver,
    FieldStatus,
    ResolvedSettings,
)
from .crypto import CredentialCipher
from .exceptions import (
    ConfigEngineError,
    CredentialDecryptionError,
    PolicyViolationError,
)
from .registry import (
    REGISTRY,
    FieldError,
    SettingSpec,
    ValidationResult,
    validate,
    validate_locked_keys,
)

__all__ = [
    "REGISTRY",
    "CascadingSettingsResolver",
    "ConfigEngineError",
    "CredentialCipher",
    "CredentialDecryptionError",
    "FieldError",
    "FieldStatus",
    "PolicyViolationError",
    "ResolvedSettings",
    "SettingSpec",
    "ValidationResult",
    "validate",
    "validate_locked_keys",
]
