"""Fail-closed configuration checks for production deployments."""

from __future__ import annotations

import os

from config_engine.environment import env_value

from api.auth.identity import is_production
from api.auth.session_manager import DEFAULT_SESSION_SECRET

_TRUTHY = ("true", "1", "yes")
MIN_SECRET_LENGTH = 32


class InsecureConfigurationError(RuntimeError):
    """Raised at startup when production is configured with unsafe defaults."""


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def production_config_problems() -> list[str]:
    """Every reason the current environment is unsafe for ``ENVIRONMENT=production``."""
    if not is_production():
        return []
    problems: list[str] = []

    session_secret = os.getenv("SESSION_SECRET") or os.getenv("NEXTAUTH_SECRET") or ""
    if not session_secret or session_secret == DEFAULT_SESSION_SECRET:
        problems.append("SESSION_SECRET (or NEXTAUTH_SECRET) must be set to a unique secret")
    elif len(session_secret) < MIN_SECRET_LENGTH:
        problems.append(f"SESSION_SECRET must be at least {MIN_SECRET_LENGTH} characters")

    if not env_value("ENCRYPTION_MASTER_KEY", "ROCKET_ENCRYPTION_KEY"):
        problems.append("ENCRYPTION_MASTER_KEY must be set; credentials cannot use a default key")

    for flag in ("DEV_AUTH_BYPASS", "ROCKET_DEV_LOGIN", "ALLOW_DEV_LOGIN"):
        if _flag(flag):
            problems.append(f"{flag} must not be enabled in production")
    return problems


def enforce_production_config() -> None:
    problems = production_config_problems()
    if problems:
        raise InsecureConfigurationError(
            "Refusing to start with an insecure production configuration: " + "; ".join(problems)
        )
