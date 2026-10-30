"""AI-generated session titles (`session_rename` / "Auto title").

The model may call the `session_rename` tool, but relying on that is unreliable: nothing
forces the main loop to spend a step on naming, so tool-based titling is effectively dead
and sessions keep their first-word placeholder. This service produces a title with a
dedicated, cheap structured-output call after the first turn — routed to the fast
``suggest`` model — and applies it only when the title is still a placeholder or was set
by the placeholder heuristic. A user rename is never clobbered.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from llm_gateway.structured import StructuredOutputError, generate_structured
from pydantic import BaseModel, Field

from api.services.session_updates import publish_session_update
from specifications.interfaces.events import EventBusProtocol
from specifications.interfaces.llm import ChatMessage, LLMGatewayProtocol, ModelRequest
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol

logger = logging.getLogger(__name__)

TITLE_SOURCE_AI = "ai"
TITLE_SOURCE_AUTO = "auto"  # set by the first-message placeholder heuristic
TITLE_SOURCE_USER = "user"

_PLACEHOLDER_TITLES = {"", "new session", "new chat", "mission", "new conversation"}
_MAX_TITLE_LENGTH = 60


class SessionTitle(BaseModel):
    """Structured output: one concise session title."""

    title: str = Field(min_length=2, max_length=_MAX_TITLE_LENGTH)


def title_source(metadata: Any) -> str | None:
    """The recorded provenance of a session's current title, if any."""
    return metadata.get("title_source") if isinstance(metadata, dict) else None


def _is_placeholder_title(title: str | None) -> bool:
    if not title:
        return True
    return title.strip().lower() in _PLACEHOLDER_TITLES or title.startswith("Mission ")


def _user_turns(conversation: Sequence[ChatMessage]) -> int:
    return sum(1 for message in conversation if message.role == "user")


def should_auto_title(session: SessionRecord) -> bool:
    """Whether the AI may (re)title this session.

    Only over the first few user turns, and only when the current title is a placeholder or
    was itself produced by the placeholder heuristic — never over a user rename.
    """
    # A single failed structured call on turn 1 (e.g. a provider that ignores the schema)
    # must not strand the session on its placeholder forever: retry over the first few
    # turns until a real title lands, or the user renames it themselves.
    if _user_turns(session.conversation_history) > 3:
        return False
    source = title_source(session.metadata)
    if source == TITLE_SOURCE_USER:
        return False
    if source == TITLE_SOURCE_AUTO or source == TITLE_SOURCE_AI:
        return True
    return _is_placeholder_title(session.title)


def sanitize_title(raw: str) -> str | None:
    """Normalise a model-produced title: strip quotes/punctuation, bound the length."""
    title = " ".join(raw.strip().strip('"').strip("'").split()).rstrip(".")
    if len(title) > _MAX_TITLE_LENGTH:
        title = title[: _MAX_TITLE_LENGTH - 3].rstrip() + "..."
    return title or None


# Transports that share a thread between people (Slack) attribute each message with a
# "[Speaker: name (<@id>)]" prefix for the agent; a title is about the request, not who sent it.
_SPEAKER_PREFIX = re.compile(r"^\s*\[Speaker:[^\]]*\]\s*")


_MENTION = re.compile(r"<[@#!][^>]*>")
_URL = re.compile(r"https?://\S+")
_FENCE = re.compile(r"```.*?(```|$)", re.DOTALL)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
PLACEHOLDER_WORDS = 7
PLACEHOLDER_CHARS = 50


def placeholder_title(prompt: str) -> str | None:
    """A readable title from a first message until the AI one arrives: its first sentence,
    without the speaker, mentions, links, code or slash commands, a few words long."""
    text = _SPEAKER_PREFIX.sub("", prompt)
    text = _FENCE.sub(" ", text)
    text = _URL.sub(" ", _MENTION.sub(" ", text))
    first = _SENTENCE_END.split(" ".join(text.split()), maxsplit=1)[0]
    words = [w for w in first.split() if not w.startswith("/") and w.strip("`*_#>-")]
    if not words:
        return None
    title = " ".join(w.strip("`*_#>") for w in words[:PLACEHOLDER_WORDS]).rstrip(".,;:!?")
    if len(title) > PLACEHOLDER_CHARS:
        title = title[: PLACEHOLDER_CHARS - 1].rstrip() + "…"
    return title[0].upper() + title[1:] if title else None


def _first_user_message(conversation: Sequence[ChatMessage]) -> str | None:
    for message in conversation:
        if message.role == "user" and message.content:
            return _SPEAKER_PREFIX.sub("", message.content) or None
    return None


def _latest_user_message(conversation: Sequence[ChatMessage]) -> str:
    for message in reversed(conversation):
        if message.role == "user" and message.content:
            return _SPEAKER_PREFIX.sub("", message.content)
    return ""


def _last_assistant_message(conversation: Sequence[ChatMessage]) -> str:
    for message in reversed(conversation):
        if message.role == "assistant" and message.content:
            return message.content
    return ""


async def generate_session_title(
    *,
    gateway: LLMGatewayProtocol,
    model: str,
    conversation: Sequence[ChatMessage],
) -> str | None:
    """Ask a cheap model for a concise title; ``None`` when it cannot be produced."""
    first_user = _first_user_message(conversation)
    if not first_user:
        return None
    request = ModelRequest(
        model=model,
        messages=[
            ChatMessage(
                role="system",
                content=(
                    "You title developer conversations for a sidebar. Reply with a specific "
                    "title of 3 to 7 words naming the task or the problem, the way an engineer "
                    "would name a ticket (for example 'Fix flaky login test in CI' or 'Add "
                    "rate limits to the export API'): a verb and its object, the component or "
                    "repository when it is named. Use the language the person writes in. No "
                    "quotes, no trailing punctuation, no names of people, never generic words "
                    "like 'Session', 'Chat', 'Help' or 'Question'."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"First request:\n{first_user[:2000]}\n\n"
                    + (
                        f"Latest request:\n{latest[:1000]}\n\n"
                        if (latest := _latest_user_message(conversation)) and latest != first_user
                        else ""
                    )
                    + f"Assistant reply:\n{_last_assistant_message(conversation)[:1500]}"
                ),
            ),
        ],
        temperature=0.2,
        max_tokens=64,
    )
    try:
        result = await generate_structured(gateway, request, SessionTitle)
    except StructuredOutputError:
        return None
    return sanitize_title(result.title)


async def apply_ai_title(
    *,
    store: SessionStoreProtocol,
    event_bus: EventBusProtocol | None,
    gateway: LLMGatewayProtocol,
    model: str,
    session: SessionRecord,
) -> str | None:
    """Generate and persist an AI title when allowed; never fail the completed turn."""
    if not should_auto_title(session):
        return None
    try:
        title = await generate_session_title(
            gateway=gateway, model=model, conversation=session.conversation_history
        )
        if not title:
            logger.warning(
                "AI title generation produced nothing for session %s; keeping the placeholder",
                session.session_id,
            )
            return None
        applied = False

        def apply_title(record: SessionRecord) -> None:
            nonlocal applied
            # Re-checked on the current record: the person may have renamed the session while
            # the title was being generated.
            if not should_auto_title(record):
                return
            record.title = title
            metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
            metadata["title_source"] = TITLE_SOURCE_AI
            record.metadata = metadata
            applied = True

        await store.mutate_session(session.session_id, session.tenant_org_id, apply_title)
        if not applied:
            return None
        session.title = title
        if event_bus is not None:
            await publish_session_update(
                event_bus, session.session_id, session.tenant_org_id, {"title": title}
            )
        return title
    except Exception:
        logger.exception("Failed to generate an AI title for session %s", session.session_id)
        return None
