"""Unit tests for the in-memory event bus and its reconnect replay history."""

from __future__ import annotations

import pytest
from api.events import AsyncIOEventBus

from specifications.interfaces.events import EventType, SessionEvent

SESSION = "session_replay_1"


def _event(payload_type: str = "token") -> SessionEvent:
    return SessionEvent(
        session_id=SESSION,
        event_type=EventType.TOKEN,
        payload={"delta": payload_type},
    )


@pytest.mark.asyncio
async def test_publish_assigns_monotonic_sequence_numbers() -> None:
    """Every published event gets the session's next sequence number."""
    bus = AsyncIOEventBus()
    first, second, third = _event(), _event(), _event()

    await bus.publish(first)
    await bus.publish(second)
    await bus.publish(third)

    assert (first.seq, second.seq, third.seq) == (1, 2, 3)
    assert bus.latest_seq(SESSION) == 3


@pytest.mark.asyncio
async def test_sequences_are_independent_per_session() -> None:
    """Two sessions do not share a counter."""
    bus = AsyncIOEventBus()
    await bus.publish(_event())
    other = SessionEvent(
        session_id="session_other", event_type=EventType.TOKEN, payload={"delta": "x"}
    )
    await bus.publish(other)

    assert bus.latest_seq(SESSION) == 1
    assert bus.latest_seq("session_other") == 1


@pytest.mark.asyncio
async def test_replay_returns_only_events_after_the_cursor() -> None:
    """A client resuming from seq N receives exactly the missed suffix, in order."""
    bus = AsyncIOEventBus()
    for index in range(5):
        await bus.publish(_event(f"kind-{index}"))

    replayed, continuous = bus.replay_since(SESSION, last_seq=2)

    assert continuous is True
    assert [event.seq for event in replayed] == [3, 4, 5]
    assert [event.payload["delta"] for event in replayed] == ["kind-2", "kind-3", "kind-4"]


@pytest.mark.asyncio
async def test_replay_is_discontinuous_when_the_cursor_was_evicted() -> None:
    """A cursor older than the retained window must be reported as discontinuous."""
    bus = AsyncIOEventBus(history_size=3)
    for _ in range(10):
        await bus.publish(_event())

    replayed, continuous = bus.replay_since(SESSION, last_seq=1)

    assert continuous is False
    assert [event.seq for event in replayed] == [8, 9, 10]


@pytest.mark.asyncio
async def test_replay_buffer_is_bounded_by_count() -> None:
    """The history keeps at most `history_size` events per session."""
    bus = AsyncIOEventBus(history_size=4)
    for _ in range(20):
        await bus.publish(_event())

    replayed, _continuous = bus.replay_since(SESSION, last_seq=0)

    assert len(replayed) == 4
    assert replayed[-1].seq == 20


@pytest.mark.asyncio
async def test_up_to_date_client_replays_nothing_and_stays_continuous() -> None:
    """Resuming at the head yields an empty, continuous replay (no snapshot refetch)."""
    bus = AsyncIOEventBus()
    await bus.publish(_event())

    replayed, continuous = bus.replay_since(SESSION, last_seq=bus.latest_seq(SESSION))

    assert replayed == []
    assert continuous is True


def test_unknown_session_replays_nothing() -> None:
    """A session with no history is continuous only if the client expects nothing."""
    bus = AsyncIOEventBus()

    assert bus.replay_since("session_absent", last_seq=0) == ([], True)
    assert bus.replay_since("session_absent", last_seq=5) == ([], False)
    assert bus.latest_seq("session_absent") == 0


@pytest.mark.asyncio
async def test_evict_drops_history_and_counter() -> None:
    """Deleting a session releases its replay buffer."""
    bus = AsyncIOEventBus()
    await bus.publish(_event())
    bus.evict(SESSION)

    assert bus.latest_seq(SESSION) == 0
    assert bus.replay_since(SESSION, last_seq=0) == ([], True)


@pytest.mark.asyncio
async def test_subscribers_receive_sequenced_events_in_order() -> None:
    """Live delivery stays ordered and sequenced after replay support was added."""
    bus = AsyncIOEventBus()
    received: list[SessionEvent] = []

    async def consume() -> None:
        async for event in bus.subscribe(SESSION):
            received.append(event)
            if len(received) == 3:
                break

    import asyncio

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    for _ in range(3):
        await bus.publish(_event())
    await task

    assert [event.seq for event in received] == [1, 2, 3]
