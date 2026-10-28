"""What a tab connecting mid-turn is shown is exactly what was published through its sequence."""

from __future__ import annotations

from types import SimpleNamespace

from api.events import AsyncIOEventBus
from api.turns.context import TurnState
from api.turns.events import TurnEventHandlers
from api.turns.running import _active_running_turns, running_turn_snapshot

from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.events import EventType

SESSION = "s_snapshot"


def _handlers() -> tuple[TurnEventHandlers, AsyncIOEventBus]:
    bus = AsyncIOEventBus()
    ctx = SimpleNamespace(state=TurnState(), req=SimpleNamespace(event_bus=bus), session_id=SESSION)
    return TurnEventHandlers(ctx), bus  # type: ignore[arg-type]


async def _stream(handlers: TurnEventHandlers, delta: str, *, reasoning: bool) -> None:
    await handlers.handle(
        AgentEvent("thought", SESSION, {"delta": delta, "is_reasoning": reasoning})
    )


def _published(bus: AsyncIOEventBus, kind: EventType, through: int) -> str:
    events, _ = bus.replay_since(SESSION, 0)
    return "".join(e.payload["delta"] for e in events if e.event_type is kind and e.seq <= through)


async def test_text_still_buffered_is_left_out_and_published_text_is_in() -> None:
    handlers, bus = _handlers()
    await _stream(handlers, "Check the ", reasoning=True)
    await _stream(handlers, "lock order.", reasoning=True)
    # Switching to answer text publishes the thought; the text itself is still buffered.
    await _stream(handlers, "The race is in", reasoning=False)

    first = await handlers.live_snapshot()

    assert first["current_reasoning"] == "Check the lock order."
    assert first["current_text"] == ""
    assert first["seq"] == bus.latest_seq(SESSION)
    for kind, field in (
        (EventType.REASONING_TOKEN, "current_reasoning"),
        (EventType.TOKEN, "current_text"),
    ):
        assert _published(bus, kind, first["seq"]) == first[field]

    await handlers.flush()
    second = await handlers.live_snapshot()

    assert second["current_text"] == "The race is in"
    assert second["seq"] > first["seq"]
    assert _published(bus, EventType.TOKEN, second["seq"]) == second["current_text"]


async def test_the_sync_snapshot_carries_the_live_part_and_its_sequence() -> None:
    handlers, _ = _handlers()
    await _stream(handlers, "Reading the config.", reasoning=True)
    await handlers.flush()
    _active_running_turns[SESSION] = {
        "turn_index": 2,
        "started_at": 1000,
        "model": "model-pro",
        "status": "running",
        "steps": [{"id": "call_1", "name": "file_read", "seq": 1}],
        "handlers": handlers,
    }
    try:
        snapshot = await running_turn_snapshot(SESSION)
    finally:
        _active_running_turns.pop(SESSION, None)

    assert snapshot is not None
    assert snapshot["current_reasoning"] == "Reading the config."
    assert snapshot["current_text"] == ""
    assert snapshot["seq"] == 1
    assert [step["id"] for step in snapshot["steps"]] == ["call_1"]


async def test_no_turn_running_here_means_no_snapshot() -> None:
    assert await running_turn_snapshot("s_nothing_runs") is None
