"""Domain models representing configuration policies and overrides across hierarchy tiers."""

from dataclasses import dataclass, field


@dataclass
class OrgPolicy:
    """Organization-level configuration and policy constraints."""

    org_id: str
    allowed_models: list[str] = field(default_factory=list)
    forbidden_models: list[str] = field(default_factory=list)
    default_model: str | None = None
    allowed_images: list[str] = field(default_factory=list)
    default_image: str | None = None
    max_memory: str | None = None
    max_cpus: float | None = None
    idle_timeout_minutes: int | None = None
    require_signed_commits: bool = False
    byok_provider: str | None = None
    byok_api_key_encrypted: str | None = None
    custom_env_vars: dict[str, str] = field(default_factory=dict)


@dataclass
class TeamPolicy:
    """Team-level configuration preferences."""

    team_id: str
    org_id: str
    default_model: str | None = None
    default_image: str | None = None
    default_repo: str | None = None
    slack_channel: str | None = None
    byok_provider: str | None = None
    byok_api_key_encrypted: str | None = None
    custom_env_vars: dict[str, str] = field(default_factory=dict)


@dataclass
class UserOverride:
    """Individual developer overrides within organization policy bounds."""

    user_id: str
    org_id: str
    team_id: str | None = None
    preferred_model: str | None = None
    preferred_image: str | None = None
    byok_provider: str | None = None
    byok_api_key_encrypted: str | None = None
    git_author_name: str | None = None
    git_author_email: str | None = None
    custom_env_vars: dict[str, str] = field(default_factory=dict)
