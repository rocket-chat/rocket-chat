"""Configuration settings for Git operations and GitHub App integration."""

import os
from dataclasses import dataclass


@dataclass
class GitEngineSettings:
    """Environment configuration for GitEngine and GitHub Webhook handling."""

    github_app_id: str | None = None
    github_app_private_key: str | None = None
    github_webhook_secret: str | None = None
    github_trigger_label: str = "ai-fix"
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"
    bot_author_name: str = "RocketChat Bot"
    bot_author_email: str = "bot@rocketchat.internal"
    default_branch: str = "main"
    # Default permission scopes when generating installation tokens dynamically
    github_app_permissions: dict[str, str] | None = None

    @classmethod
    def from_env(cls) -> "GitEngineSettings":
        """Instantiate settings from environment variables."""
        # Scopes configurable via comma-separated list or JSON (defaults to contents:read,pull_requests:read)
        raw_perms = os.getenv("GITHUB_APP_DEFAULT_PERMISSIONS", "contents:read,pull_requests:read")
        perms_dict: dict[str, str] = {}
        if raw_perms.strip():
            for item in raw_perms.split(","):
                if ":" in item:
                    k, v = item.strip().split(":", 1)
                    perms_dict[k.strip()] = v.strip()

        return cls(
            github_app_id=os.getenv("GITHUB_APP_ID"),
            github_app_private_key=os.getenv("GITHUB_APP_PRIVATE_KEY"),
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET"),
            github_trigger_label=os.getenv("GITHUB_TRIGGER_LABEL", "ai-fix"),
            github_token=os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_PAT"),
            github_api_url=os.getenv("GITHUB_API_URL", "https://api.github.com"),
            bot_author_name=os.getenv("GIT_BOT_NAME", "RocketChat Bot"),
            bot_author_email=os.getenv("GIT_BOT_EMAIL", "bot@rocketchat.internal"),
            default_branch=os.getenv("GIT_DEFAULT_BRANCH", "main"),
            github_app_permissions=perms_dict,
        )
