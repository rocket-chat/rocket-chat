"""Setting a bot up in Slack: checking its tokens and generating its app manifest.

Slack apps can't be created without a configuration token, so Rocket Chat generates the
manifest, the administrator creates and installs the app at api.slack.com, then pastes the two
tokens back. ``auth.test`` then says which workspace and bot user they belong to.
"""

from __future__ import annotations

import logging
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from api.services.slack_bots import (
    SlackBotError,
    SlackBotRecord,
    SlackIdentity,
    SlackTokens,
    slash_command_of,
)

logger = logging.getLogger(__name__)

SLACK_TIMEOUT_SECONDS = 10

_BASE_SCOPES = (
    "app_mentions:read",
    "assistant:write",
    "channels:history",
    "channels:read",
    "chat:write",
    "commands",
    "files:read",
    "files:write",
    "groups:history",
    "groups:read",
    "im:history",
    "im:read",
    "im:write",
    "mpim:history",
    "reactions:read",
    "reactions:write",
    "users:read",
    "users:read.email",
)
_BASE_EVENTS = (
    "app_home_opened",
    "app_mention",
    "assistant_thread_context_changed",
    "assistant_thread_started",
    "message.im",
)


async def check_tokens(tokens: SlackTokens) -> SlackIdentity:
    """Who the tokens belong to; refuses tokens of the wrong kind or that Slack rejects."""
    if not tokens.bot_token.startswith("xoxb-"):
        raise SlackBotError("The bot token starts with xoxb-.")
    if not tokens.app_token.startswith("xapp-"):
        raise SlackBotError("The app-level token starts with xapp-.")
    bot = AsyncWebClient(token=tokens.bot_token, timeout=SLACK_TIMEOUT_SECONDS)
    try:
        auth = await bot.auth_test()
        info = await bot.bots_info(bot=str(auth.get("bot_id", "")))
        # Opening a Socket Mode URL is the only check of an app-level token; nothing connects.
        await AsyncWebClient(
            token=tokens.app_token, timeout=SLACK_TIMEOUT_SECONDS
        ).apps_connections_open(app_token=tokens.app_token)
    except SlackApiError as err:
        raise SlackBotError(f"Slack refused the tokens: {err.response.get('error')}") from err
    except Exception as err:
        logger.warning("Slack token check failed: %s", type(err).__name__)
        raise SlackBotError("Slack could not be reached to check the tokens.") from err
    bot_info: dict[str, Any] = info.get("bot") or {}
    bot_user_id = str(auth.get("user_id", ""))
    return SlackIdentity(
        team_id=str(auth.get("team_id", "")),
        team_name=str(auth.get("team", "")),
        bot_user_id=bot_user_id,
        app_id=str(bot_info.get("app_id", "")),
        handle=await _display_name(bot, bot_user_id) or str(auth.get("user", "")),
    )


async def _display_name(bot: AsyncWebClient, bot_user_id: str) -> str:
    """The name people see and mention (the manifest's ``bot_user.display_name``).

    ``auth.test`` returns the bot user's username, which Slack fixes when the app is created
    and keeps when the display name changes, so it can name the app's first identity.
    """
    try:
        profile = ((await bot.users_info(user=bot_user_id)).get("user") or {}).get("profile") or {}
    except Exception as err:
        logger.info("Bot display name unavailable: %s", type(err).__name__)
        return ""
    return str(profile.get("display_name") or profile.get("real_name") or "").strip()


def manifest(record: SlackBotRecord, web_url: str = "") -> dict[str, Any]:
    """The Slack app manifest for this bot: its name, scopes and events from its behaviour."""
    events = list(_BASE_EVENTS)
    if record.listen_channels:
        events += ["message.channels", "message.groups"]
    handle = (record.handle or record.name).lower().replace(" ", "-")[:80]
    description = f"Rocket Chat agent: {record.name}"
    body: dict[str, Any] = {
        "display_information": {"name": record.name[:35], "description": description[:140]},
        "features": {
            "app_home": {
                "home_tab_enabled": True,
                "messages_tab_enabled": record.respond_in_dms,
                "messages_tab_read_only_enabled": False,
            },
            "assistant_view": {"assistant_description": description[:140]},
            "bot_user": {"display_name": handle, "always_online": True},
            "slash_commands": [
                {
                    "command": slash_command_of(record),
                    "description": "Start, list or stop work",
                    "usage_hint": "new <task> | status | stop | pair",
                    "should_escape": False,
                },
                {
                    "command": "/rocket-pair",
                    "description": "Link your Rocket Chat account",
                    "should_escape": False,
                },
            ],
            "shortcuts": [
                {
                    "name": "Ask Rocket",
                    "type": "message",
                    "callback_id": "ask_rocket",
                    "description": "Ask the agent about this message",
                }
            ],
        },
        "oauth_config": {"scopes": {"bot": list(_BASE_SCOPES)}},
        "settings": {
            "event_subscriptions": {"bot_events": sorted(events)},
            "interactivity": {"is_enabled": True},
            "org_deploy_enabled": False,
            "socket_mode_enabled": True,
            "token_rotation_enabled": False,
        },
    }
    if web_url:
        body["display_information"]["long_description"] = (
            f"{description}. Sessions open in Rocket Chat at {web_url}. Mention the bot in a "
            "channel, or message it directly, to start work in a thread."
        )
    return body
