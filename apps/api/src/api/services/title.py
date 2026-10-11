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
from collections.abc import Sequence
from typing import Any

from llm_gateway.structured import StructuredOutputError, generate_structured
from pydantic import BaseModel, Field

from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent
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


def _first_user_message(conversation: Sequence[ChatMessage]) -> str | None:
    for message in conversation:
        if message.role == "user" and message.content:
            return message.content
    return None


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
                    "You title developer conversations. Reply with a concise, specific title "
                    "of 3 to 6 words describing the task. No quotes, no trailing punctuation, "
                    "and never generic words like 'Session', 'Chat' or 'Help'."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"First user message:\n{first_user[:2000]}\n\n"
                    f"Assistant reply:\n{_last_assistant_message(conversation)[:1500]}"
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
            await event_bus.publish(
                SessionEvent(
                    session_id=session.session_id,
                    event_type=EventType.SESSION_UPDATED,
                    payload={"title": title, "session_id": session.session_id},
                )
            )
        return title
    except Exception:
        logger.exception("Failed to generate an AI title for session %s", session.session_id)
        return None
