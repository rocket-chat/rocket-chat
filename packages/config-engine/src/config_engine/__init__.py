"""Hierarchical Configuration & Policy Engine for Rocket Chat."""

from .cascading import (
    CascadingSettingsResolver,
    ResolvedSettings,
)
from .crypto import CredentialCipher
from .exceptions import (
    ConfigEngineError,
    CredentialDecryptionError,
    PolicyViolationError,
)
from .policy import Policy, Preset, ResolveContext, Resolved, resolve
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
    "Policy",
    "PolicyViolationError",
    "Preset",
    "ResolveContext",
    "Resolved",
    "ResolvedSettings",
    "SettingSpec",
    "ValidationResult",
    "resolve",
    "validate",
    "validate_locked_keys",
]
