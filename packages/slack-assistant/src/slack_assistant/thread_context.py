"""Slack thread & channel context retrieval for the Slack Assistant.

Builds bounded, speaker-attributed transcripts so the agent can reason over an
entire thread (or prior channel history) even when individual messages did not
mention the bot. All transcripts are clamped to a character budget to protect
the model context window.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_CHARS = 6000


@dataclass
class SlackMessage:
    """Normalized Slack message used for transcript rendering."""

    ts: str
    user: str
    text: str
    is_bot: bool = False
    is_root: bool = False


def _unwrap(response: Any) -> dict[str, Any]:
    """Normalize slack-bolt ``SlackResponse`` objects and raw dicts into a plain dict."""
    if response is None:
        return {}
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return data
    if isinstance(response, dict):
        return response
    return {}


def _to_messages(raw_messages: Any, bot_user_id: str | None = None) -> list[SlackMessage]:
    """Convert raw Slack API message payloads into normalized ``SlackMessage`` objects."""
    if not isinstance(raw_messages, list):
        return []
    messages: list[SlackMessage] = []
    for raw in raw_messages:
        if not isinstance(raw, dict):
            continue
        user = raw.get("user") or raw.get("bot_id") or ""
        is_bot = bool(raw.get("bot_id")) or (bot_user_id is not None and user == bot_user_id)
        messages.append(
            SlackMessage(
                ts=str(raw.get("ts", "")),
                user=user,
                text=raw.get("text", "") or "",
                is_bot=is_bot,
            )
        )
    return messages


async def fetch_thread_messages(
    client: Any,
    channel_id: str,
    thread_ts: str,
    limit: int = 50,
    bot_user_id: str | None = None,
) -> list[SlackMessage]:
    """Fetch messages belonging to a Slack thread (including the root message)."""
    response = await client.conversations_replies(channel=channel_id, ts=thread_ts, limit=limit)
    return _to_messages(_unwrap(response).get("messages", []), bot_user_id)


async def fetch_channel_history(
    client: Any,
    channel_id: str,
    limit: int = 50,
    latest: str | None = None,
    bot_user_id: str | None = None,
) -> list[SlackMessage]:
    """Fetch the most recent messages from a channel, optionally older than ``latest``."""
    kwargs: dict[str, Any] = {"channel": channel_id, "limit": limit}
    if latest:
        kwargs["latest"] = latest
    response = await client.conversations_history(**kwargs)
    # Chronological order (oldest first) reads most naturally in a transcript.
    return list(reversed(_to_messages(_unwrap(response).get("messages", []), bot_user_id)))


async def render_transcript(
    messages: list[SlackMessage],
    name_resolver: Callable[[str], Awaitable[str]],
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Render messages into a speaker-attributed transcript clamped to ``max_chars``.

    The most recent messages are always preserved; older messages are dropped
    first so the freshest conversational context is never lost.
    """
    names: dict[str, str] = {}
    for message in messages:
        if message.user and message.user not in names:
            try:
                names[message.user] = await name_resolver(message.user)
            except Exception:
                names[message.user] = message.user

    lines: list[str] = []
    for message in messages:
        text = (message.text or "").strip()
        if not text:
            continue
        if message.is_bot:
            speaker = names.get(message.user, "Rocket") + " (bot)"
        else:
            speaker = names.get(message.user, message.user or "unknown")
        lines.append(f"- {speaker}: {text}")

    selected: list[str] = []
    total = 0
    for line in reversed(lines):
        if total + len(line) + 1 > max_chars and selected:
            selected.append("… (earlier messages truncated)")
            break
        selected.append(line)
        total += len(line) + 1

    return "\n".join(reversed(selected))


async def build_thread_transcript(
    client: Any,
    channel_id: str,
    thread_ts: str,
    name_resolver: Callable[[str], Awaitable[str]],
    limit: int = 20,
    max_chars: int = DEFAULT_MAX_CHARS,
    bot_user_id: str | None = None,
) -> str:
    """Fetch and render a bounded transcript of the current Slack thread."""
    messages = await fetch_thread_messages(
        client, channel_id, thread_ts, limit=limit, bot_user_id=bot_user_id
    )
    return await render_transcript(messages, name_resolver, max_chars=max_chars)


async def build_channel_transcript(
    client: Any,
    channel_id: str,
    name_resolver: Callable[[str], Awaitable[str]],
    limit: int = 50,
    latest: str | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    bot_user_id: str | None = None,
) -> str:
    """Fetch and render a bounded transcript of recent channel history."""
    messages = await fetch_channel_history(
        client, channel_id, limit=limit, latest=latest, bot_user_id=bot_user_id
    )
    return await render_transcript(messages, name_resolver, max_chars=max_chars)
