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
    # Development only: accept webhooks when no secret is configured. Never honoured by
    # the API in production, where an unsigned webhook would let anyone start agent runs.
    allow_unsigned_webhooks: bool = False
    # fnmatch patterns ("org/*") of repositories whose events may start sessions; empty = any.
    allowed_repos: tuple[str, ...] = ()
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"
    # The App's URL slug, used to build the "install on GitHub" link for an organization.
    github_app_slug: str | None = None
    # Legacy single-tenant behaviour: mint tokens for whichever installation GitHub lists first
    # or finds for a repository, without an organization's explicit mapping. Off by default -
    # with several organizations it would hand one the other's repository access.
    allow_unmapped_installations: bool = False
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

        if not perms_dict:
            perms_dict = {
                "contents": "read",
                "pull_requests": "read",
            }

        return cls(
            github_app_id=os.getenv("GITHUB_APP_ID"),
            github_app_private_key=os.getenv("GITHUB_APP_PRIVATE_KEY"),
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET"),
            github_trigger_label=os.getenv("GITHUB_TRIGGER_LABEL", "ai-fix"),
            allow_unsigned_webhooks=os.getenv("ROCKET_ALLOW_UNSIGNED_WEBHOOKS", "").lower()
            in ("true", "1", "yes")
            and os.getenv("ENVIRONMENT", "").lower() != "production",
            allowed_repos=tuple(
                p.strip() for p in os.getenv("GITHUB_ALLOWED_REPOS", "").split(",") if p.strip()
            ),
            github_token=os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_PAT"),
            github_api_url=os.getenv("GITHUB_API_URL", "https://api.github.com"),
            github_app_slug=os.getenv("GITHUB_APP_SLUG") or None,
            allow_unmapped_installations=os.getenv(
                "GITHUB_APP_ALLOW_UNMAPPED_INSTALLATIONS", ""
            ).lower()
            in ("true", "1", "yes"),
            bot_author_name=os.getenv("GIT_BOT_NAME", "RocketChat Bot"),
            bot_author_email=os.getenv("GIT_BOT_EMAIL", "bot@rocketchat.internal"),
            default_branch=os.getenv("GIT_DEFAULT_BRANCH", "main"),
            github_app_permissions=perms_dict,
        )
