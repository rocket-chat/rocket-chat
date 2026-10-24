"""Unit tests for progressive loading (tail messages) and thought stream continuity."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from api.models import SessionResponse
from api.routers.session_crud import get_session, get_session_messages
from api.services.session_support import _to_response
from api.sockets.session_socket import SessionSocket

from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import SessionRecord, SessionSource


def _dummy_session(message_count: int = 5) -> SessionRecord:
    history = [
        ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"msg-{i}")
        for i in range(message_count)
    ]
    return SessionRecord(
        session_id="s_test",
        tenant_org_id="org_test",
        tenant_user_id="user_test",
        title="Test Session",
        source=SessionSource.WEB_UI,
        created_at=1000,
        updated_at=1000,
        conversation_history=history,
    )


@pytest.mark.asyncio
async def test_to_response_tail_and_pagination() -> None:
    rec = _dummy_session(5)
    resp = _to_response(
        rec,
        history_override=rec.conversation_history[-2:],
        has_older_history=True,
        total_messages=5,
    )
    assert isinstance(resp, SessionResponse)
    assert len(resp.conversation_history) == 2
    assert resp.conversation_history[0]["content"] == "msg-3"
    assert resp.has_older_history is True
    assert resp.total_messages == 5


def _fake_req(store: Any) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_store=store)),
        headers={
            "X-Dev-Tenant-Org": "org_test",
            "X-Dev-Tenant-User": "user_test",
            "X-Dev-Roles": "member",
        },
        cookies={},
        state=SimpleNamespace(
            tenant_org_id="org_test",
            tenant_user_id="user_test",
            live_roles={"member"},
        ),
    )


@pytest.mark.asyncio
async def test_get_session_endpoint_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _dummy_session(6)
    store = SimpleNamespace(get_session=AsyncMock(return_value=rec))
    req = _fake_req(store)
    monkeypatch.setattr(
        "api.routers.session_crud._get_request_identity",
        lambda _r: ("org_test", "user_test", {"member"}),
    )

    # Calling with tail=2
    res = await get_session("s_test", req, tail=2)  # type: ignore[arg-type]
    assert len(res.conversation_history) == 2
    assert res.has_older_history is True
    assert res.total_messages == 6
    assert res.conversation_history[0]["content"] == "msg-4"

    # Calling with no tail returns all
    full = await get_session("s_test", req, tail=None)  # type: ignore[arg-type]
    assert len(full.conversation_history) == 6
    assert full.has_older_history is False
    assert full.total_messages == 6


@pytest.mark.asyncio
async def test_get_session_messages_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _dummy_session(10)
    store = SimpleNamespace(get_session=AsyncMock(return_value=rec))
    req = _fake_req(store)
    monkeypatch.setattr(
        "api.routers.session_crud._get_request_identity",
        lambda _r: ("org_test", "user_test", {"member"}),
    )

    page = await get_session_messages("s_test", req, before_index=8, limit=3)  # type: ignore[arg-type]
    assert page.total == 10
    assert page.start_index == 5
    assert page.has_more is True
    assert len(page.messages) == 3
    assert page.messages[0]["content"] == "msg-5"
    assert page.messages[2]["content"] == "msg-7"


@pytest.mark.asyncio
async def test_socket_reads_current_reasoning_from_running_turn() -> None:
    from api.turns.runner import _active_running_turns

    fake_ws = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                session_store=SimpleNamespace(),
                event_bus=SimpleNamespace(),
                orchestrator=SimpleNamespace(),
            )
        )
    )
    handler = SessionSocket(fake_ws, "s_active")  # type: ignore[arg-type]

    # Mock an active running turn with partial reasoning in flight
    fake_ctx = SimpleNamespace(
        state=SimpleNamespace(
            reasoning_parts=["Thought 1. ", "Thought 2."],
            reasoning_attached=0,
            executed_tools=[],
        )
    )
    _active_running_turns["s_active"] = {
        "turn_index": 1,
        "started_at": 1000,
        "model": "model-pro",
        "status": "running",
        "steps": [],
        "ctx": fake_ctx,
    }

    try:
        running = await handler._read_running_turn()
        assert running is not None
        assert running["current_reasoning"] == "Thought 1. Thought 2."
        assert running["status"] == "running"
    finally:
        _active_running_turns.pop("s_active", None)
