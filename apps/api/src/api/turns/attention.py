"""What a session needs from its people, kept on the record so the sidebar can say so.

A turn paused for an approval or a question is easy to lose track of in a long list, and a turn
that failed looks the same as one that finished. Two keys of the session's metadata carry what
a person scanning the list needs: ``awaiting_input`` (``approval`` or ``question`` while a gate
is open) and ``last_turn_status`` (how the latest turn ended). Every change is published as a
``session_updated`` event, so open sidebars follow without polling.
"""

from __future__ import annotations

from typing import Any

from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol

AWAITING_INPUT_KEY = "awaiting_input"
LAST_TURN_STATUS_KEY = "last_turn_status"


async def update_attention(
    store: SessionStoreProtocol,
    event_bus: EventBusProtocol,
    session_id: str,
    org_id: str,
    *,
    awaiting_input: str | None = None,
    last_turn_status: str | None = None,
    clear_awaiting: bool = False,
) -> None:
    """Record what the session waits on (or that it no longer waits) and how its turn ended."""

    def apply(record: SessionRecord) -> None:
        known = record.metadata if isinstance(record.metadata, dict) else {}
        changed = dict(known)
        if awaiting_input:
            changed[AWAITING_INPUT_KEY] = awaiting_input
        elif clear_awaiting:
            changed.pop(AWAITING_INPUT_KEY, None)
        if last_turn_status:
            changed[LAST_TURN_STATUS_KEY] = last_turn_status
        record.metadata = changed

    await store.mutate_session(session_id, org_id, apply)
    payload: dict[str, Any] = {"session_id": session_id}
    if awaiting_input or clear_awaiting:
        payload["awaiting_input"] = awaiting_input or ""
    if last_turn_status:
        payload["last_turn_status"] = last_turn_status
    await event_bus.publish(
        SessionEvent(session_id=session_id, event_type=EventType.SESSION_UPDATED, payload=payload)
    )
