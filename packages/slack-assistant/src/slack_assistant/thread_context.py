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


def _object_text(obj: Any) -> str:
    """The text of a Block Kit text object, or of a context element that carries one."""
    if isinstance(obj, dict) and obj.get("type") in ("mrkdwn", "plain_text"):
        return str(obj.get("text") or "").strip()
    return ""


def _blocks_text(blocks: Any) -> str:
    """What a message's header, section and context blocks show, one line per block."""
    if not isinstance(blocks, list):
        return ""
    lines: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "header":
            text = _object_text(block.get("text"))
            lines.append(f"*{text}*" if text else "")
        elif kind == "section":
            lines.append(_object_text(block.get("text")))
            lines.extend(_object_text(field) for field in block.get("fields") or [])
        elif kind == "context":
            lines.append(" · ".join(t for t in map(_object_text, block.get("elements") or []) if t))
        elif kind == "divider":
            lines.append("---")
    return "\n".join(line for line in lines if line)


def _attachments_text(attachments: Any) -> str:
    """What a message's legacy attachments show (alerting bots still post them)."""
    if not isinstance(attachments, list):
        return ""
    parts: list[str] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        lines = [str(attachment.get(key) or "").strip() for key in ("pretext", "title", "text")]
        for field in attachment.get("fields") or []:
            if isinstance(field, dict) and (field.get("title") or field.get("value")):
                lines.append(f"{field.get('title') or ''}: {field.get('value') or ''}".strip(": "))
        lines.append(_blocks_text(attachment.get("blocks")))
        if not any(lines):
            lines.append(str(attachment.get("fallback") or "").strip())
        parts.append("\n".join(line for line in lines if line))
    return "\n".join(part for part in parts if part)


def message_text(raw: dict[str, Any]) -> str:
    """What a Slack message shows: its blocks when it has some, as Slack then only keeps
    ``text`` for notifications, else its text, followed by its attachments. Messages typed
    by people carry their text as rich_text blocks, which ``text`` already renders."""
    body = _blocks_text(raw.get("blocks")) or str(raw.get("text") or "")
    attachments = _attachments_text(raw.get("attachments"))
    return "\n".join(part for part in (body.strip(), attachments) if part)


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
                text=message_text(raw),
                is_bot=is_bot,
            )
        )
    return messages


# ``conversations.replies`` returns a thread oldest first, a page at a time: reaching its newest
# messages means reading every page. Bounded, so a runaway thread costs a known number of calls.
REPLIES_PAGE_SIZE = 200
MAX_REPLY_PAGES = 10


async def fetch_thread_messages(
    client: Any,
    channel_id: str,
    thread_ts: str,
    limit: int = 50,
    bot_user_id: str | None = None,
) -> list[SlackMessage]:
    """The thread's root message and its ``limit`` most recent replies, oldest first."""
    raw: list[Any] = []
    cursor: str | None = None
    for _ in range(MAX_REPLY_PAGES):
        kwargs: dict[str, Any] = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": REPLIES_PAGE_SIZE,
        }
        if cursor:
            kwargs["cursor"] = cursor
        data = _unwrap(await client.conversations_replies(**kwargs))
        page = data.get("messages", [])
        raw.extend(page if isinstance(page, list) else [])
        metadata = data.get("response_metadata")
        cursor = metadata.get("next_cursor") if isinstance(metadata, dict) else None
        if not cursor or not data.get("has_more", True):
            break
    messages = _to_messages(raw, bot_user_id)
    if not messages:
        return []
    root = next((m for m in messages if m.ts == thread_ts), None)
    replies = [m for m in messages if m is not root]
    recent = replies[-limit:] if limit > 0 else []
    return ([root] if root is not None else []) + recent


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


_CUT = " … (message truncated)"


def _within(line: str, max_chars: int) -> str:
    if len(line) + 1 <= max_chars:
        return line
    return line[: max(max_chars - len(_CUT) - 1, 0)] + _CUT


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
        # The freshest message is always kept whole, so a bot report of fifty blocks would
        # carry the budget away by itself: it is cut to the budget instead.
        line = _within(line, max_chars)
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
    include_own_replies: bool = True,
) -> str:
    """Fetch and render a bounded transcript of the current Slack thread.

    ``include_own_replies=False`` leaves out this bot's messages: as context for a turn, its
    answers are already in the session's history, and would be read twice.
    """
    messages = await fetch_thread_messages(
        client, channel_id, thread_ts, limit=limit, bot_user_id=bot_user_id
    )
    if not include_own_replies and bot_user_id:
        messages = [m for m in messages if m.user != bot_user_id]
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
