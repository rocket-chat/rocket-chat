"""Generic Cascading Settings Engine supporting Org Defaults, User Overrides, and Admin Lockouts."""

from dataclasses import dataclass, field
from typing import Any, Literal

FieldStatus = Literal["org_default", "overridden", "locked"]

SYSTEM_DOMAIN_DEFAULTS: dict[str, dict[str, Any]] = {
    "github": {
        "commit_authorship_policy": "co_authored",  # "co_authored", "user_only", "bot_only"
        "pr_creation_policy": "as_user",  # "as_user", "as_bot"
        "commit_signing_mode": "github_app",  # "github_app", "gpg_key", "none"
        "bot_author_name": "RocketChat Bot",
        "bot_author_email": "bot@rocketchat.internal",
        "default_branch": "main",
        "app_token_permissions": {
            "contents": "read",
            "pull_requests": "read",
        },
        "app_token_target_repositories": "all",  # "all" or specific comma-separated list
    },
    "mcp": {
        "allowed_transports": ["sse", "http", "stdio"],
        "user_allowed_transports": ["sse", "http"],
        "auto_bind_enforced": True,
        "max_user_servers": 10,
    },
    "slack": {
        "default_agent_persona": "code_architect",
        "allowed_channel_types": ["public", "private", "im", "mpim"],
        "enable_interactive_session_switcher": True,
        "auto_link_by_verified_email": True,
        "allowed_tools": [
            "web_fetch",
            "web_search",
            "read_file",
            "session_rename",
            "update_task_checklist",
        ],
    },
    "models": {
        "default_model": "openrouter/deepseek/deepseek-v4.1-flash",
        "fallback_models": [
            "openrouter/google/gemini-2.5-pro",
            "openrouter/openai/gpt-4o",
        ],
        "temperature": 0.2,
        "max_tokens": 8192,
    },
    "sandboxes": {
        "default_image": "python:3.12-slim",
        "cpu_limit": "2.0",
        "memory_limit": "4Gi",
        "timeout_seconds": 600,
        "allowed_images": [
            "python:3.12-slim",
            "node:20-slim",
            "rust:1.77-slim",
            "golang:1.22-bookworm",
            "ubuntu:22.04",
        ],
    },
    "general": {
        "company_name": "Acme Labs",
        "compliance_tier": "SOC2",
        "session_privacy_default": "private",  # "private" or "shared_org"
        "telemetry_level": "standard",
        "thrust_animation_enabled": True,
        "require_signed_commits": True,
        "suggest_next_questions": True,
    },
    "user_preferences": {
        "full_name": "Alex Turner",
        "default_role": "Flight Director",
        "git_author_name": "Alex Turner",
        "git_author_email": "alex.turner@acme.internal",
        "git_branch_prefix": "alex/rocket-",
        "git_personal_pat": "ghp_live_pat_finegrained",
        "slack_user_handle": "@alex",
        "slack_notifications_enabled": True,
        "preferred_model": "openrouter/deepseek/deepseek-v4.1-flash",
        "turbo_mode": True,
        "theme_mode": "dark",
        "notification_sound": True,
        "suggest_next_questions": True,
    },
}


@dataclass
class ResolvedSettings:
    """Resolved cascading configuration with inheritance tracking and locked key enforcement."""

    domain: str
    effective: dict[str, Any]
    field_status: dict[str, FieldStatus]
    locked_keys: list[str] = field(default_factory=list)
    org_defaults: dict[str, Any] = field(default_factory=dict)
    user_overrides: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "domain": self.domain,
            "effective": self.effective,
            "field_status": self.field_status,
            "locked_keys": self.locked_keys,
            "org_defaults": self.org_defaults,
            "user_overrides": self.user_overrides,
        }


class CascadingSettingsResolver:
    """Resolves effective settings following: System Defaults -> Org Policy -> User Override.

    Enforces that keys present in `locked_keys` cannot be overridden by user configurations.
    """

    def __init__(self, custom_defaults: dict[str, dict[str, Any]] | None = None) -> None:
        self.defaults = dict(SYSTEM_DOMAIN_DEFAULTS)
        if custom_defaults:
            for domain, conf in custom_defaults.items():
                self.defaults.setdefault(domain, {}).update(conf)

    def get_system_defaults(self, domain: str) -> dict[str, Any]:
        """Retrieve system default configuration for a given domain."""
        return dict(self.defaults.get(domain, {}))

    def resolve(
        self,
        domain: str,
        org_config: dict[str, Any] | None = None,
        locked_keys: list[str] | None = None,
        user_overrides: dict[str, Any] | None = None,
    ) -> ResolvedSettings:
        """Calculate effective configuration, field status annotations, and enforce locked keys."""
        system_base = self.get_system_defaults(domain)
        org_conf = dict(org_config or {})
        locked = list(locked_keys or [])
        user_over = dict(user_overrides or {})

        # Step 1: Base org defaults = system_base merged with org_conf
        merged_org: dict[str, Any] = dict(system_base)
        merged_org.update(org_conf)

        effective: dict[str, Any] = dict(merged_org)
        field_status: dict[str, FieldStatus] = {}

        # Default all known keys to org_default
        for key in merged_org:
            field_status[key] = "locked" if key in locked else "org_default"

        # Step 2: Apply user overrides where not locked
        for key, value in user_over.items():
            if key in locked:
                # User cannot override locked keys
                field_status[key] = "locked"
                # Keep org value in effective
                effective[key] = merged_org.get(key, value)
            else:
                effective[key] = value
                field_status[key] = "overridden"

        return ResolvedSettings(
            domain=domain,
            effective=effective,
            field_status=field_status,
            locked_keys=locked,
            org_defaults=merged_org,
            user_overrides=user_over,
        )

    def filter_valid_user_overrides(
        self,
        domain: str,
        requested_overrides: dict[str, Any],
        locked_keys: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        """Separate allowed user overrides from disallowed locked keys.

        Returns:
            (allowed_overrides, rejected_locked_keys)
        """
        allowed: dict[str, Any] = {}
        rejected: list[str] = []

        for key, value in requested_overrides.items():
            if key in locked_keys:
                rejected.append(key)
            else:
                allowed[key] = value

        return allowed, rejected
