"""Instructions the engine gives the model during a turn, outside the conversation.

Budget notices, checklist reminders, the "you wrote a tool call as text" notice and the final
synthesis instructions are about *this* request. They used to be stored in the conversation as
``system`` messages, which outlived the turn and, on providers whose translation moves every
system message into the system prompt (LiteLLM does so for Anthropic- and Gemini-style APIs),
turned "do not call any more tools" into a standing rule for every later turn.

They are now rendered at the end of each request only, as one user-role reminder, and never
stored. Only the leading messages of a request are ever ``system``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from specifications.interfaces.llm import ChatMessage

REMINDER_OPEN = "<system-reminder>"
REMINDER_CLOSE = "</system-reminder>"


def render_notes(notes: Sequence[str]) -> str:
    body = "\n\n".join(note.strip() for note in notes if note.strip())
    return f"{REMINDER_OPEN}\n{body}\n{REMINDER_CLOSE}"


def with_turn_notes(messages: list[ChatMessage], notes: Sequence[str]) -> list[ChatMessage]:
    """``messages`` with ``notes`` appended as a trailing reminder (request-local copy).

    A request that already ends with the user's own message gets the reminder inside that
    message, so two user turns never follow each other; otherwise it is a user message of its
    own after the last tool result or assistant reply.
    """
    if not any(note.strip() for note in notes):
        return messages
    reminder = render_notes(notes)
    last = messages[-1] if messages else None
    # A message carrying the cache breakpoint keeps exactly its stored text: the reminder goes
    # in a message of its own after it (providers join adjacent user messages into one turn).
    if last is not None and last.role == "user" and not last.tool_calls and not last.cacheable:
        merged = replace(last, content=f"{last.content or ''}\n\n{reminder}".strip())
        return [*messages[:-1], merged]
    return [*messages, ChatMessage(role="user", content=reminder)]


def is_engine_note(message: ChatMessage) -> bool:
    """Whether a stored message is a control notice an earlier version wrote into the history."""
    return message.role == "system"


def without_engine_notes(messages: Sequence[ChatMessage]) -> list[ChatMessage]:
    """The conversation without the ``system`` notices older transcripts stored mid-history."""
    return [message for message in messages if not is_engine_note(message)]


def with_cache_breakpoint(messages: list[ChatMessage]) -> list[ChatMessage]:
    """``messages`` with a prompt-cache breakpoint on the last conversation message (a copy).

    For providers that take explicit cache markers (Anthropic-style ``cache_control``) the
    stable system prompt and the tools carry one already; this rolling one makes each request
    of a turn read the whole history the previous request wrote. Providers that cache prefixes
    automatically ignore the flag. Assistant messages that only call tools have no text to
    carry a marker, so the last message with text is marked.
    """
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role != "system" and message.content and not message.attachments:
            return [*messages[:index], replace(message, cacheable=True), *messages[index + 1 :]]
    return messages
