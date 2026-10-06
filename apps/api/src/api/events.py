"""In-memory asynchronous event bus implementing EventBusProtocol."""

import asyncio
from collections.abc import AsyncIterator

from specifications.interfaces.events import EventBusProtocol, SessionEvent


class AsyncIOEventBus(EventBusProtocol):
    """Broadcaster distributing session events to active WebSocket connections."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[SessionEvent]]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, event: SessionEvent) -> None:
        """Publish a session event to all connected subscriber queues."""
        async with self._lock:
            queues = list(self._subscribers.get(event.session_id, set()))

        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(
        self,
        session_id: str,
        client_id: str | None = None,
    ) -> AsyncIterator[SessionEvent]:
        """Subscribe to real-time events for a specific session."""
        queue: asyncio.Queue[SessionEvent] = asyncio.Queue(maxsize=500)
        async with self._lock:
            if session_id not in self._subscribers:
                self._subscribers[session_id] = set()
            self._subscribers[session_id].add(queue)

        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            async with self._lock:
                if session_id in self._subscribers:
                    self._subscribers[session_id].discard(queue)
                    if not self._subscribers[session_id]:
                        del self._subscribers[session_id]
