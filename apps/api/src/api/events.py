"""In-memory asynchronous event bus implementing EventBusProtocol.

Includes a bounded per-session replay history so a reconnecting client can resume from
the last event it applied instead of silently missing everything published while it was
disconnected. The history is bounded by *count*: it is a reconnect buffer, not an audit
log (durable per-turn traces are a separate concern).
"""

import asyncio
import os
from collections import deque
from collections.abc import AsyncIterator, Callable

from pydantic import ConfigDict, TypeAdapter

from specifications.interfaces.event_payloads import PAYLOAD_TYPES
from specifications.interfaces.events import EventBusProtocol, SessionEvent

DEFAULT_HISTORY_SIZE = 2000


def _payload_validators() -> dict[object, TypeAdapter[object]]:
    strict = ConfigDict(extra="forbid")
    adapters: dict[object, TypeAdapter[object]] = {}
    for event_type, payload_type in PAYLOAD_TYPES.items():
        payload_type.__pydantic_config__ = strict
        adapters[event_type] = TypeAdapter(payload_type)
    return adapters


# Contract violations seen while ``ROCKET_VALIDATE_EVENTS`` is on. They are recorded rather than
# raised: a publisher failing inside a background turn would hang the test instead of
# failing it, so the test harness asserts this list is empty after every test.
CONTRACT_VIOLATIONS: list[str] = []


def validate_event(event: SessionEvent, validators: dict[object, TypeAdapter[object]]) -> None:
    adapter = validators.get(event.event_type)
    if adapter is None:
        CONTRACT_VIOLATIONS.append(f"no payload contract for event type {event.event_type!r}")
        return
    try:
        adapter.validate_python(event.payload)
    except Exception as err:
        first = str(err).splitlines()[:3]
        CONTRACT_VIOLATIONS.append(f"{event.event_type.value}: {' | '.join(first)}")


class AsyncIOEventBus(EventBusProtocol):
    """Broadcaster distributing session events to active WebSocket connections."""

    def __init__(self, history_size: int = DEFAULT_HISTORY_SIZE) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[SessionEvent]]] = {}
        self._history: dict[str, deque[SessionEvent]] = {}
        self._last_seq: dict[str, int] = {}
        self._history_size = history_size
        self._lock = asyncio.Lock()
        # Off in production (it costs a validation per event); the test suite turns it on so
        # every published payload is checked against its contract.
        self._validators = _payload_validators() if os.getenv("ROCKET_VALIDATE_EVENTS") else None

    async def publish(self, event: SessionEvent) -> None:
        """Publish a session event to all connected subscriber queues.

        Assigns the session's next sequence number first, so subscribers and the replay
        history agree on ordering even when publishes interleave.
        """
        if self._validators is not None:
            validate_event(event, self._validators)
        async with self._lock:
            self._last_seq[event.session_id] = self._last_seq.get(event.session_id, 0) + 1
            event.seq = self._last_seq[event.session_id]
            history = self._history.setdefault(event.session_id, self._new_history())
            history.append(event)
            queues = list(self._subscribers.get(event.session_id, set()))

        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _new_history(self) -> deque[SessionEvent]:
        return deque(maxlen=self._history_size)

    async def attach(self, session_id: str) -> None:
        """Prepare to serve ``session_id``; a no-op for a single-process bus."""

    def latest_seq(self, session_id: str) -> int:
        """Sequence number of the newest event published for this session."""
        return self._last_seq.get(session_id, 0)

    def replay_since(self, session_id: str, last_seq: int) -> tuple[list[SessionEvent], bool]:
        """Events published after ``last_seq``, plus whether the replay is continuous.

        Continuity fails in two cases: the requested point predates the oldest retained
        event (the buffer wrapped), or the client's cursor is *ahead* of this bus (the
        process restarted and lost its history). Either way the caller must refetch the
        session snapshot rather than trust an incomplete stream.
        """
        latest = self.latest_seq(session_id)
        if last_seq > latest:
            return [], False

        history = self._history.get(session_id)
        if not history:
            # Nothing to replay and the cursor is within range: already up to date.
            return [], True

        oldest_seq = history[0].seq
        events = [event for event in history if event.seq > last_seq]
        continuous = last_seq >= oldest_seq - 1
        return events, continuous

    def evict(self, session_id: str) -> None:
        """Drop a session's history and sequence counter (session deleted/archived)."""
        self._history.pop(session_id, None)
        self._last_seq.pop(session_id, None)

    def open_queue(self, session_id: str) -> tuple[asyncio.Queue[SessionEvent], Callable[[], None]]:
        queue: asyncio.Queue[SessionEvent] = asyncio.Queue(maxsize=500)
        self._subscribers.setdefault(session_id, set()).add(queue)

        def close() -> None:
            subscribers = self._subscribers.get(session_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    del self._subscribers[session_id]

        return queue, close

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
