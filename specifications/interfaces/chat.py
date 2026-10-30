"""Chat platforms besides Slack (Microsoft Teams, Mattermost) behind one contract.

A platform adapter turns its events into ``IncomingChatMessage`` values and posts text into a
thread (``ChatPlatformProtocol``); ``chat_bridges.ChatBridge`` does the rest the same way for every
platform: admission, the session of the thread, the shared turn pipeline, and the turn's
progress, questions, approvals and answer rendered as messages. Slack keeps its own richer
integration (``slack_assistant``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

CHAT_PLATFORMS: tuple[str, ...] = ("teams", "mattermost")


@dataclass(frozen=True)
class IncomingChatMessage:
    """A message addressed to the bot on a chat platform."""

    platform: str
    # The conversation (channel, chat) and the thread in it the bot replies in.
    conversation_id: str
    thread_id: str
    message_id: str
    user_id: str
    user_name: str
    text: str
    # The platform's tenant (a Teams tenant, a Mattermost team); "" when it has none.
    tenant_id: str = ""
    # A one-to-one conversation with the bot.
    direct: bool = False


class ChatPlatformProtocol(Protocol):
    name: str
    # The longest message the platform accepts; longer text is split.
    max_message_chars: int

    async def reply(self, conversation_id: str, thread_id: str, text: str) -> str:
        """Post markdown ``text`` in the thread; the new message's id."""
        ...

    async def edit(self, conversation_id: str, message_id: str, text: str) -> None:
        """Replace the text of a message the bot posted."""
        ...
