"""
Interface definitions for Hierarchical Configuration & Policy Engine.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ModelPolicy:
    allowed_models: list[str] = field(
        default_factory=list
    )  # Wildcards supported e.g. "anthropic/*"
    forbidden_models: list[str] = field(default_factory=list)
    default_model: str = "openrouter/deepseek/deepseek-v4.1-flash"


@dataclass
class SandboxPolicy:
    allowed_images: list[str] = field(default_factory=list)
    default_image: str = "ghcr.io/platform/dev-base:latest"
    max_memory: str = "8Gi"
    max_cpus: float = 4.0
    idle_timeout_minutes: int = 30


@dataclass
class ResolvedConfig:
    """The final calculated configuration applied to an agent session."""

    active_model: str
    active_container_image: str
    byok_provider: str
    byok_api_key: str
    git_author_name: str
    git_author_email: str
    require_signed_commits: bool
    idle_timeout_minutes: int
    custom_env_vars: dict[str, str] = field(default_factory=dict)


class ConfigEngineProtocol(ABC):
    """
    Contract for resolving System Defaults -> Org Policy -> Team Policy -> User Overrides.
    """

    @abstractmethod
    async def resolve_session_config(
        self,
        tenant_org_id: str,
        team_id: str | None,
        user_id: str,
    ) -> ResolvedConfig:
        """
        Calculates the effective runtime configuration, raising PolicyViolationError
        if a user or team override violates an organization constraint.
        """
        pass

    @abstractmethod
    async def set_user_byok(
        self,
        user_id: str,
        provider: str,
        api_key: str,
    ) -> None:
        """Encrypts and persists a user's personal BYOK credential."""
        pass
