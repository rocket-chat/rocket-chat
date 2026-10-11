"""AI session titling (`session_rename` / "Auto title").

Relying on the model to call `session_rename` never fired, so sessions kept a first-word
placeholder. Titles are now produced by a dedicated structured call after the first turn,
applied only while the title is a placeholder and never over a user rename.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from api.services.title import (
    TITLE_SOURCE_AI,
    TITLE_SOURCE_AUTO,
    TITLE_SOURCE_USER,
    apply_ai_title,
    generate_session_title,
    sanitize_title,
    should_auto_title,
)
from api.store import InMemorySessionStore

from specifications.interfaces.events import EventType
from specifications.interfaces.llm import LLMGatewayProtocol, ModelRequest, StreamChunk
from specifications.interfaces.session import SessionRecord


class _TitleGateway(LLMGatewayProtocol):
    def __init__(self, payload: str = '{"title": "Fix Auth Token Bug"}') -> None:
        self.payload = payload
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        yield StreamChunk(text_delta=self.payload)
        yield StreamChunk(is_finished=True, finish_reason="stop")

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        return None


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


def _session(**overrides: Any) -> SessionRecord:
    from specifications.interfaces.llm import ChatMessage

    defaults: dict[str, Any] = {
        "session_id": "session_title",
        "tenant_org_id": "org_a",
        "tenant_user_id": "user_a",
        "title": "New Session",
        "conversation_history": [ChatMessage(role="user", content="Fix the auth token bug")],
        "metadata": {},
    }
    defaults.update(overrides)
    return SessionRecord(**defaults)


def test_sanitize_title_strips_noise_and_bounds_length() -> None:
    assert sanitize_title('  "Fix Auth Token Bug."  ') == "Fix Auth Token Bug"
    assert len(sanitize_title("word " * 40) or "") <= 60


@pytest.mark.asyncio
async def test_generate_session_title_uses_structured_output() -> None:
    gateway = _TitleGateway()
    title = await generate_session_title(
        gateway=gateway, model="fast", conversation=_session().conversation_history
    )
    assert title == "Fix Auth Token Bug"
    # The dedicated call is constrained to the title schema.
    assert gateway.requests[0].response_schema is not None


def test_should_auto_title_only_on_first_turn_and_placeholder() -> None:
    assert should_auto_title(_session()) is True
    assert should_auto_title(_session(metadata={"title_source": TITLE_SOURCE_AUTO})) is True
    assert should_auto_title(_session(metadata={"title_source": TITLE_SOURCE_USER})) is False
    assert should_auto_title(_session(title="Fix Auth Bug", metadata={})) is False
    from specifications.interfaces.llm import ChatMessage

    # The titler retries over the first few turns (a provider may ignore the schema on
    # turn 1), but must not keep trying forever.
    two_turns = [
        ChatMessage(role="user", content="one"),
        ChatMessage(role="assistant", content="a"),
        ChatMessage(role="user", content="two"),
    ]
    assert should_auto_title(_session(conversation_history=two_turns)) is True
    many_turns = []
    for index in range(4):
        many_turns.append(ChatMessage(role="user", content=f"turn {index}"))
        many_turns.append(ChatMessage(role="assistant", content="a"))
    assert should_auto_title(_session(conversation_history=many_turns)) is False


@pytest.mark.asyncio
async def test_apply_ai_title_persists_and_broadcasts() -> None:
    store = InMemorySessionStore()
    session = _session()
    await store.create_session(session)
    bus = _RecordingBus()

    applied = await apply_ai_title(
        store=store,
        event_bus=bus,  # type: ignore[arg-type]
        gateway=_TitleGateway(),
        model="fast",
        session=session,
    )

    assert applied == "Fix Auth Token Bug"
    stored = await store.get_session(session.session_id)
    assert stored is not None
    assert stored.title == "Fix Auth Token Bug"
    assert stored.metadata["title_source"] == TITLE_SOURCE_AI
    assert any(e.event_type == EventType.SESSION_UPDATED for e in bus.events)


@pytest.mark.asyncio
async def test_apply_ai_title_never_overwrites_a_user_rename() -> None:
    store = InMemorySessionStore()
    session = _session(title="My Own Title", metadata={"title_source": TITLE_SOURCE_USER})
    await store.create_session(session)

    applied = await apply_ai_title(
        store=store,
        event_bus=None,
        gateway=_TitleGateway(),
        model="fast",
        session=session,
    )

    assert applied is None
    stored = await store.get_session(session.session_id)
    assert stored is not None
    assert stored.title == "My Own Title"
