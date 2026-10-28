"""Where a session stands, told to its own subscribers and to its organization's sidebars."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from specifications.interfaces.events import (
    EventBusProtocol,
    EventType,
    SessionEvent,
    organization_channel,
)


async def publish_session_update(
    event_bus: EventBusProtocol, session_id: str, org_id: str, payload: Mapping[str, Any]
) -> None:
    """Publish ``session_updated`` on the session's topic and on its organization's.

    The sidebar socket checks, event by event, that its member may see the session before
    relaying it, so the organization topic carries nothing a member is not shown already.
    """
    body = {**payload, "session_id": session_id}
    await event_bus.publish(
        SessionEvent(session_id=session_id, event_type=EventType.SESSION_UPDATED, payload=body)
    )
    await event_bus.publish(
        SessionEvent(
            session_id=organization_channel(org_id),
            event_type=EventType.SESSION_UPDATED,
            payload=dict(body),
        )
    )
