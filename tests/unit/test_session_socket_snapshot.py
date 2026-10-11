"""The connect snapshot loses nothing, however slow the session read is."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import api.sockets.session_socket as module
import pytest
from api.events import AsyncIOEventBus
from api.sockets.session_socket import SessionSocket

from specifications.interfaces.events import EventType, SessionEvent

SESSION_ID = "session_x"


def _status(status: str, *, final: bool | None = None) -> SessionEvent:
    """A status event; terminal statuses are the runner's closing event unless said otherwise."""
    is_final = status != "running" if final is None else final
    payload: dict[str, object] = {"status": status}
    if is_final:
        payload["final"] = True
    return SessionEvent(session_id=SESSION_ID, event_type=EventType.STATUS_CHANGED, payload=payload)


def _token(delta: str) -> SessionEvent:
    return SessionEvent(session_id=SESSION_ID, event_type=EventType.TOKEN, payload={"delta": delta})


def _socket(bus: AsyncIOEventBus, store: Any) -> SessionSocket:
    socket = SessionSocket.__new__(SessionSocket)
    socket._session_id = SESSION_ID
    socket._event_bus = bus
    socket._store = store
    socket._db = None
    socket._stored_decision = None
    socket._session = SimpleNamespace(session_id=SESSION_ID)
    return socket


@pytest.fixture(autouse=True)
def _plain_session_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "asdict", lambda value: {"session_id": value.session_id})


def test_snapshot_is_synchronous() -> None:
    assert not asyncio.iscoroutinefunction(SessionSocket._snapshot)


@pytest.mark.asyncio
async def test_a_turn_in_flight_is_replayed_from_its_start() -> None:
    bus = AsyncIOEventBus()
    for event in (_status("completed"), _status("running"), _token("Hel"), _token("lo")):
        await bus.publish(event)
    frame, floor = _socket(bus, None)._snapshot(seq_before_read=bus.latest_seq(SESSION_ID))
    assert floor == 4
    assert frame["catch_up"]["from_seq"] == 1
    assert [e["payload"].get("delta") for e in frame["catch_up"]["events"]] == [None, "Hel", "lo"]


@pytest.mark.asyncio
async def test_repeated_running_statuses_do_not_move_the_start_of_the_turn() -> None:
    bus = AsyncIOEventBus()
    for event in (
        _status("completed"),
        _status("running"),
        _token("first "),
        _status("running"),
        _token("second"),
    ):
        await bus.publish(event)
    frame, _ = _socket(bus, None)._snapshot(seq_before_read=bus.latest_seq(SESSION_ID))
    deltas = [e["payload"].get("delta") for e in frame["catch_up"]["events"]]
    assert deltas == [None, "first ", None, "second"]


@pytest.mark.asyncio
async def test_an_idle_session_needs_no_catch_up() -> None:
    bus = AsyncIOEventBus()
    for event in (_status("running"), _token("done"), _status("completed")):
        await bus.publish(event)
    frame, _ = _socket(bus, None)._snapshot(seq_before_read=bus.latest_seq(SESSION_ID))
    assert frame["catch_up"] is None


@pytest.mark.asyncio
async def test_a_turn_finishing_during_the_slow_session_read_is_not_lost() -> None:
    bus = AsyncIOEventBus()
    await bus.publish(_status("completed"))

    async def slow_get_session(session_id: str) -> Any:
        # The turn runs to completion while the record is being read: the record read
        # started before its answer was stored.
        for event in (_status("running"), _token("answer"), _status("completed")):
            await bus.publish(event)
        await asyncio.sleep(0.01)
        return SimpleNamespace(session_id=session_id)

    socket = _socket(bus, SimpleNamespace(get_session=slow_get_session))
    queue, close = bus.open_queue(SESSION_ID)
    seq_before_read = bus.latest_seq(SESSION_ID)
    await socket._load_baseline()
    frame, floor = socket._snapshot(seq_before_read)
    close()

    deltas = [e["payload"].get("delta") for e in frame["catch_up"]["events"]]
    assert "answer" in deltas
    assert frame["catch_up"]["from_seq"] == seq_before_read
    assert floor == bus.latest_seq(SESSION_ID)
    assert queue.qsize() == 3


@pytest.mark.asyncio
async def test_the_live_stream_skips_what_the_snapshot_already_carried() -> None:
    bus = AsyncIOEventBus()
    queue, close = bus.open_queue(SESSION_ID)
    for event in (_token("old"), _token("new")):
        await bus.publish(event)
    sent: list[dict[str, Any]] = []
    delivered = asyncio.Event()

    async def send_json(frame: dict[str, Any]) -> None:
        sent.append(frame)
        delivered.set()

    socket = _socket(bus, None)
    socket._ws = SimpleNamespace(send_json=send_json)  # type: ignore[assignment]
    task = asyncio.create_task(socket._stream_events(queue, floor_seq=1))
    await asyncio.wait_for(delivered.wait(), timeout=1)
    task.cancel()
    close()
    assert [f["payload"]["delta"] for f in sent] == ["new"]


@pytest.mark.asyncio
async def test_the_orchestrators_completion_does_not_end_the_turn_before_the_answer_is_stored() -> (
    None
):
    bus = AsyncIOEventBus()
    for event in (
        _status("running"),
        _token("answer"),
        # The orchestrator reports completion first; the runner stores the answer and only
        # then publishes its own closing event.
        SessionEvent(
            session_id=SESSION_ID,
            event_type=EventType.STATUS_CHANGED,
            payload={"state": "completed", "status": "completed", "message": "Turn complete."},
        ),
    ):
        await bus.publish(event)
    frame, _ = _socket(bus, None)._snapshot(seq_before_read=bus.latest_seq(SESSION_ID))
    deltas = [e["payload"].get("delta") for e in frame["catch_up"]["events"]]
    assert "answer" in deltas, "the tokens must reach a client that connects at this moment"

    await bus.publish(_status("completed"))
    after, _ = _socket(bus, None)._snapshot(seq_before_read=bus.latest_seq(SESSION_ID))
    assert after["catch_up"] is None, "once the runner closed the turn the record is complete"
