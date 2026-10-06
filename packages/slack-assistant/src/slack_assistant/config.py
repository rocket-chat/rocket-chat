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
