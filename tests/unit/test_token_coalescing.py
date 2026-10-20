"""Unit tests for streaming token coalescing and ordering in TurnEventHandlers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from api.turns.context import TurnContext, TurnRequest, TurnState
from api.turns.events import TokenCoalescer, TurnEventHandlers

from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.session import SessionRecord


@pytest.mark.asyncio
async def test_token_coalescer_batches_by_char_limit() -> None:
    published: list[tuple[EventType, dict[str, str]]] = []

    async def mock_publish(event_type: EventType, payload: dict[str, str]) -> None:
        published.append((event_type, payload))

    coalescer = TokenCoalescer(mock_publish, max_chars=20, max_delay_ms=1000.0)

    # Add 15 chars - should not flush yet
    await coalescer.add(EventType.TOKEN, "Hello, world! ")
    assert len(published) == 0

    # Add 10 more chars - exceeds 20 chars, flushes immediately
    await coalescer.add(EventType.TOKEN, "1234567890")
    assert len(published) == 1
    assert published[0][0] == EventType.TOKEN
    assert published[0][1]["delta"] == "Hello, world! 1234567890"

    # Flush when empty does nothing
    await coalescer.flush()
    assert len(published) == 1


@pytest.mark.asyncio
async def test_token_coalescer_flushes_on_type_change() -> None:
    published: list[tuple[EventType, dict[str, str]]] = []

    async def mock_publish(event_type: EventType, payload: dict[str, str]) -> None:
        published.append((event_type, payload))

    coalescer = TokenCoalescer(mock_publish, max_chars=100, max_delay_ms=1000.0)

    await coalescer.add(EventType.REASONING_TOKEN, "Thinking step 1... ")
    assert len(published) == 0

    # Switching to text token flushes reasoning tokens first
    await coalescer.add(EventType.TOKEN, "Here is the answer.")
    assert len(published) == 1
    assert published[0][0] == EventType.REASONING_TOKEN
    assert published[0][1]["delta"] == "Thinking step 1... "

    await coalescer.flush()
    assert len(published) == 2
    assert published[1][0] == EventType.TOKEN
    assert published[1][1]["delta"] == "Here is the answer."


@pytest.mark.asyncio
async def test_token_coalescer_flushes_on_timer() -> None:
    published: list[tuple[EventType, dict[str, str]]] = []

    async def mock_publish(event_type: EventType, payload: dict[str, str]) -> None:
        published.append((event_type, payload))

    coalescer = TokenCoalescer(mock_publish, max_chars=100, max_delay_ms=50.0)

    await coalescer.add(EventType.TOKEN, "Partial")
    assert len(published) == 0

    # Wait for the 50ms timer to fire
    await asyncio.sleep(0.08)
    assert len(published) == 1
    assert published[0][1]["delta"] == "Partial"


@pytest.mark.asyncio
async def test_turn_event_handlers_flushes_before_non_token_events() -> None:
    published_events: list[SessionEvent] = []

    event_bus = MagicMock()

    async def capture_publish(event: SessionEvent) -> None:
        event.seq = len(published_events) + 1
        published_events.append(event)

    event_bus.publish = AsyncMock(side_effect=capture_publish)
    event_bus.latest_seq.side_effect = lambda sid: len(published_events)

    ctx = MagicMock(spec=TurnContext)
    ctx.session_id = "sess-123"
    ctx.session = SessionRecord(
        session_id="sess-123", tenant_org_id="org-1", tenant_user_id="user-1", title="Test"
    )
    ctx.state = TurnState()
    ctx.trace = MagicMock()
    ctx.audit = MagicMock()
    ctx.audit.record_tool_call = AsyncMock()
    ctx.redactor = MagicMock()
    ctx.redactor.redact = MagicMock(return_value=None)
    ctx.req = MagicMock(spec=TurnRequest)
    ctx.req.event_bus = event_bus
    ctx.req.db_mgr = None

    handlers = TurnEventHandlers(ctx)

    # 1. Stream token
    await handlers.handle(
        AgentEvent(
            session_id="sess-123",
            event_type="thought",
            payload={"delta": "Let me check...", "is_reasoning": False},
        )
    )
    assert len(published_events) == 0

    # 2. Tool call arrives - should flush the buffered token FIRST
    await handlers.handle(
        AgentEvent(
            session_id="sess-123",
            event_type="tool_call",
            payload={"id": "call_1", "name": "bash_exec", "arguments": '{"command": "ls"}'},
        )
    )
    assert len(published_events) >= 2
    assert published_events[0].event_type == EventType.TOKEN
    assert published_events[0].payload["delta"] == "Let me check..."
    assert published_events[1].event_type == EventType.TOOL_STARTED
    assert published_events[1].payload["name"] == "bash_exec"

    # 3. Tool completed
    await handlers.handle(
        AgentEvent(
            session_id="sess-123",
            event_type="tool_completed",
            payload={
                "id": "call_1",
                "name": "bash_exec",
                "result": "file.txt",
                "status": "completed",
                "duration_ms": 10,
            },
        )
    )
    tool_completed_event = next(
        e for e in published_events if e.event_type == EventType.TOOL_COMPLETED
    )
    assert tool_completed_event.payload["result"] == "file.txt"
    assert len(ctx.state.executed_tools) == 1
    assert ctx.state.executed_tools[0]["seq"] == tool_completed_event.seq

    # 4. Final tokens and turn flush
    await handlers.handle(
        AgentEvent(
            session_id="sess-123",
            event_type="thought",
            payload={"delta": "Done!", "is_reasoning": False},
        )
    )
    await handlers.flush()
    assert published_events[-1].event_type == EventType.TOKEN
    assert published_events[-1].payload["delta"] == "Done!"


@pytest.mark.asyncio
async def test_timer_flushes_never_lose_or_reorder_text_behind_a_slow_publisher() -> None:
    """The timer's flush cancelled its own task mid-publish: the chunk was taken from the buffer
    and never sent, so live thoughts showed sentences with words missing."""
    published: list[str] = []

    async def slow_publish(event_type: EventType, payload: dict[str, str]) -> None:
        await asyncio.sleep(0.005)
        published.append(payload["delta"])

    coalescer = TokenCoalescer(slow_publish, max_chars=10_000, max_delay_ms=5.0)
    words = [f"word{i} " for i in range(200)]
    for word in words:
        await coalescer.add(EventType.REASONING_TOKEN, word)
        await asyncio.sleep(0.001)
    await coalescer.flush()
    await asyncio.sleep(0.05)

    assert "".join(published) == "".join(words)
    assert len(published) > 1, "the timer did flush along the way"
