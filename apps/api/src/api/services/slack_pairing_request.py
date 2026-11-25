"""Pairing a Slack user from the web, for a conversation their guest started.

A Slack user who never paired owns their direct messages through a read-only guest nobody can
sign in as, so the person cannot open them in the cockpit. They are not made public: the
cockpit offers to send the owner a pairing link *in the conversation itself*, through the bot
that holds it, so only that Slack user ever receives the code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from api.services.slack_bot_setup import SLACK_TIMEOUT_SECONDS
from api.services.slack_bots import SlackBotStore
from api.services.slack_link import PairingCodes, pairing_link, slack_guest_of
from specifications.interfaces.session import SessionRecord


class PairingRequestError(Exception):
    """A refusal the route turns into an HTTP error."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class PairingTarget:
    """Where to reach the Slack user who owns a session: their bot, their DM, their identity."""

    slack_user_id: str
    team_id: str
    bot_id: str
    channel_id: str
    session_title: str


async def pairing_target(
    db_mgr: Any, org_id: str, record: SessionRecord, caller_id: str
) -> PairingTarget | None:
    """The Slack owner of a direct-message session the caller does not own, or ``None``."""
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    bot_id = str(metadata.get("slack_bot_id") or "")
    channel_id = str(metadata.get("slack_channel_id") or "")
    if record.tenant_user_id == caller_id or not (metadata.get("slack_is_dm") and bot_id):
        return None
    guest = await slack_guest_of(db_mgr, org_id, record.tenant_user_id)
    if guest is None or not channel_id:
        return None
    return PairingTarget(
        slack_user_id=guest[1],
        team_id=guest[0],
        bot_id=bot_id,
        channel_id=channel_id,
        session_title=record.title,
    )


def _message(link: str, requester: str, title: str) -> str:
    return (
        ":link: *Link your Rocket Chat account*\n"
        f"{requester} is trying to open this conversation (“{title}”) in Rocket Chat. It was "
        "started here before your Slack account was linked, so only you can open it.\n"
        f"<{link}|Link your account and open it> (valid 10 minutes, single use). "
        "If this was not you or someone you know, ignore this message."
    )


async def send_pairing_request(
    *,
    target: PairingTarget,
    codes: PairingCodes,
    bot_store: SlackBotStore,
    org_id: str,
    base_url: str,
    session_id: str,
    requester: str,
) -> None:
    """Issue a code for the Slack user and post its link in their DM with the right bot."""
    if await codes.issued_recently(target.slack_user_id):
        raise PairingRequestError(
            429, "A link was sent a moment ago. Check your Slack messages before asking again."
        )
    tokens = await bot_store.tokens(org_id, target.bot_id)
    if tokens is None:
        raise PairingRequestError(409, "The Slack bot of this conversation is no longer configured")
    code = await codes.begin(target.slack_user_id, target.team_id)
    link = pairing_link(base_url, code, session_id)
    client = AsyncWebClient(token=tokens.bot_token, timeout=SLACK_TIMEOUT_SECONDS)
    try:
        await client.chat_postMessage(
            channel=target.channel_id,
            text=_message(link, requester, target.session_title),
            unfurl_links=False,
        )
    except SlackApiError as err:
        raise PairingRequestError(
            502, f"Slack refused the message: {err.response.get('error')}"
        ) from err
