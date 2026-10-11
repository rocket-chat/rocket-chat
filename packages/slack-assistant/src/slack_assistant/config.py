"""Configuration settings for Slack Assistant."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SlackAssistantSettings(BaseSettings):
    """Runtime configuration for Slack Socket Mode integration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    slack_bot_token: str = Field(
        default="",
        validation_alias="SLACK_BOT_TOKEN",
        description="Slack bot user OAuth token (xoxb-...).",
    )
    slack_app_token: str = Field(
        default="",
        validation_alias="SLACK_APP_TOKEN",
        description="Slack app-level token with connections:write scope for Socket Mode (xapp-...).",
    )
    auto_hibernate_seconds: int = Field(
        default=1800,
        description="Inactivity timeout before hibernating thread sandbox containers.",
    )
    slack_thread_context_limit: int = Field(
        default=20,
        description=(
            "Maximum number of recent messages from the current Slack thread "
            "automatically injected into the agent system prompt as context."
        ),
    )
    slack_thread_context_max_chars: int = Field(
        default=6000,
        description="Character budget for the auto-injected Slack thread transcript.",
    )
    slack_channel_history_limit: int = Field(
        default=50,
        description=(
            "Maximum number of messages returned by the on-demand slack_read_channel_history tool."
        ),
    )

    # --- Unauthenticated-path hardening -------------------------------------
    slack_default_org_id: str = Field(
        default="default_org",
        validation_alias="SLACK_DEFAULT_ORG_ID",
        description=(
            "Organization that owns Slack workspaces no administrator has bound to one "
            "(single-organization deployments need no binding)."
        ),
    )
    slack_team_id: str = Field(
        default="",
        validation_alias="SLACK_TEAM_ID",
        description=(
            "Fallback Slack workspace id used to scope quotas and rate limits when an event "
            "does not carry a team id (the live event normally does)."
        ),
    )
    slack_allowed_tools: str = Field(
        default="*",
        validation_alias="SLACK_ALLOWED_TOOLS",
        description=(
            "Comma-separated tool allowlist for the unauthenticated Slack path; '*' allows "
            "every tool except those in SLACK_BLOCKED_TOOLS. Enforced server-side."
        ),
    )
    slack_blocked_tools: str = Field(
        default="git_revert,git_open_pr",
        validation_alias="SLACK_BLOCKED_TOOLS",
        description=(
            "Comma-separated destructive tools that are always denied on the Slack path, "
            "even when SLACK_ALLOWED_TOOLS is '*'."
        ),
    )
    slack_max_attachments_per_turn: int = Field(
        default=5,
        validation_alias="SLACK_MAX_ATTACHMENTS_PER_TURN",
        description="Maximum uploaded Slack files ingested for a single turn; the rest are skipped.",
    )
    slack_max_consecutive_failures: int = Field(
        default=3,
        validation_alias="SLACK_MAX_CONSECUTIVE_FAILURES",
        description=(
            "Consecutive failed turns for one workspace before the abuse circuit breaker "
            "pauses further dispatch (0 disables the breaker)."
        ),
    )
    slack_failure_cooldown_seconds: int = Field(
        default=300,
        validation_alias="SLACK_FAILURE_COOLDOWN_SECONDS",
        description="Cooldown after the consecutive-failure breaker trips before retrying.",
    )
    slack_user_turns_per_minute: int = Field(
        default=12,
        validation_alias="SLACK_USER_TURNS_PER_MINUTE",
        description="Sustained per-user turn budget per minute (token-bucket capacity).",
    )
    slack_channel_turns_per_minute: int = Field(
        default=30,
        validation_alias="SLACK_CHANNEL_TURNS_PER_MINUTE",
        description="Sustained per-channel turn budget per minute (token-bucket capacity).",
    )
    slack_proactive_notify_after_seconds: int = Field(
        default=120,
        validation_alias="SLACK_PROACTIVE_NOTIFY_AFTER_SECONDS",
        description=(
            "Post an explicit completion notice into the originating thread when a turn "
            "runs longer than this many seconds."
        ),
    )
    slack_file_download_timeout_seconds: float = Field(
        default=30.0,
        validation_alias="SLACK_FILE_DOWNLOAD_TIMEOUT_SECONDS",
        description="Download timeout for Slack-hosted files ingested into the workspace.",
    )

    slack_health_check_seconds: float = Field(
        default=30.0,
        validation_alias="SLACK_HEALTH_CHECK_SECONDS",
        description=(
            "How often the Socket Mode watchdog checks the connection; two failed checks in a "
            "row reconnect it (0 disables the watchdog)."
        ),
    )
    slack_web_base_url: str = Field(
        default="",
        validation_alias="ROCKET_PUBLIC_URL",
        description=(
            "Public address of the Rocket Chat web app; Slack threads link to their session "
            "there. An organization can override it with the `slack.web_base_url` setting."
        ),
    )

    # --- Outbound capability policy (org-configurable in the back-office) ---------------
    slack_dm_policy: str = Field(
        default="paired",
        validation_alias="SLACK_DM_POLICY",
        description=(
            "Who the agent may DM: 'none' (disabled), 'paired' (only Slack users linked to a "
            "Rocket account), or 'all' (any member of the workspace). Default: paired."
        ),
    )
    slack_outbound_channels: str = Field(
        default="",
        validation_alias="SLACK_OUTBOUND_CHANNELS",
        description=(
            "Comma-separated allowlist of channel ids the agent may post to. Empty means no "
            "restriction (the DM policy still applies to direct messages)."
        ),
    )
