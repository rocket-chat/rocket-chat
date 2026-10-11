"""Integration test for WebSocket reconnect replay (``resume`` / ``last_seq``).

Reproduces the real failure mode this feature exists to prevent: the socket drops while
a turn is still running, the turn keeps publishing server-side, and on reconnect the
client must receive exactly the events it missed — no gap and no duplicates.
"""

import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.events import AsyncIOEventBus
from api.main import app
from api.store import InMemorySessionStore
from fastapi.testclient import TestClient

from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk
from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionRecord

HEADERS = {
    "X-Tenant-Org-Id": "default_org",
    "X-Tenant-User-Id": "dev_user",
    "X-User-Role": "developer",
}
FINAL_TEXT = "Replay test turn complete."


class ScriptedGateway(LLMGatewayProtocol):
    """Streams a fixed answer, so a turn is deterministic and finishes in milliseconds."""

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        for piece in ("Replay test turn ", "complete."):
            yield StreamChunk(text_delta=piece)
        yield StreamChunk(is_finished=True, finish_reason="stop")

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


def _wire_app(session_id: str) -> None:
    """Install a scripted orchestrator, sandbox stub, store and a fresh event bus."""
    driver = MagicMock(spec=SandboxDriverProtocol)
    driver.ensure_workspace = AsyncMock()
    driver.start_sandbox = AsyncMock()
    driver.exec_command = AsyncMock()
    driver.read_file = AsyncMock(return_value="content")

    registry = ToolRegistry(driver=driver, session_id=session_id)
    store = InMemorySessionStore()
    store._sessions[session_id] = SessionRecord(
        session_id=session_id,
        tenant_org_id="default_org",
        tenant_user_id="dev_user",
        title="Replay Test",
    )

    app.state.sandbox_driver = driver
    app.state.llm_gateway = ScriptedGateway()
    app.state.tool_registry = registry
    app.state.orchestrator = AsyncReActOrchestrator(gateway=ScriptedGateway(), registry=registry)
    app.state.event_bus = AsyncIOEventBus()
    app.state.session_store = store


def _read_type(ws: Any, message_type: str, limit: int = 60) -> dict[str, Any]:
    """Read frames until one of the given type arrives."""
    for _ in range(limit):
        frame = ws.receive_json()
        if frame.get("type") == message_type:
            return frame
    raise AssertionError(f"no {message_type} frame received")


def _await_turn_completion(client: TestClient, session_id: str) -> None:
    """Poll the session until the assistant's answer is persisted."""
    deadline = time.time() + 20
    while time.time() < deadline:
        record = client.get(f"/v1/sessions/{session_id}", headers=HEADERS).json()
        history = record.get("conversation_history") or []
        if any(FINAL_TEXT.rstrip(".") in (message.get("content") or "") for message in history):
            return
        time.sleep(0.05)
    raise AssertionError("the turn never completed while the client was offline")


def test_resume_replays_the_turn_missed_while_offline() -> None:
    """A turn that runs while nobody is attached is replayed on resume, exactly once."""
    session_id = "session_replay_offline"
    _wire_app(session_id)
    client = TestClient(app)

    with client.websocket_connect(f"/v1/sessions/{session_id}/ws?token=dev_token") as ws:
        sync = ws.receive_json()
        assert sync["type"] == "sync"
        cursor = sync["last_seq"]

        # Submit a turn and drop the socket immediately, without reading a single frame:
        # the laptop sleeping mid-turn.
        ws.send_json({"type": "turn", "content": "Summarise the retry policy"})

    _await_turn_completion(client, session_id)

    with client.websocket_connect(f"/v1/sessions/{session_id}/ws?token=dev_token") as ws:
        sync = ws.receive_json()
        assert sync["type"] == "sync"
        assert sync["last_seq"] > cursor, "the missed turn must have advanced the sequence"

        ws.send_json({"type": "resume", "last_seq": cursor})
        replay = _read_type(ws, "replay")

        assert replay["continuous"] is True
        assert replay["last_seq"] == sync["last_seq"]
        event_types = {event["event_type"] for event in replay["events"]}
        # The whole turn is replayed: status transitions and the streamed answer tokens.
        assert "status_changed" in event_types
        assert "token" in event_types
        assert all(event["seq"] > cursor for event in replay["events"])
        assert [event["seq"] for event in replay["events"]] == sorted(
            event["seq"] for event in replay["events"]
        )

        # Resuming again from the new head replays nothing: no duplicate delivery.
        ws.send_json({"type": "resume", "last_seq": sync["last_seq"]})
        empty_replay = _read_type(ws, "replay")
        assert empty_replay["events"] == []
        assert empty_replay["continuous"] is True


def test_resume_with_a_cursor_ahead_of_the_server_is_discontinuous() -> None:
    """A cursor this process never issued (restart) must force a snapshot refetch."""
    session_id = "session_replay_stale"
    _wire_app(session_id)
    client = TestClient(app)

    with client.websocket_connect(f"/v1/sessions/{session_id}/ws?token=dev_token") as ws:
        ws.receive_json()
        ws.send_json({"type": "resume", "last_seq": 99_999})
        replay = _read_type(ws, "replay")
        assert replay["continuous"] is False


def test_sync_hands_out_the_current_sequence() -> None:
    """The handshake tells the client where the stream currently stands."""
    session_id = "session_replay_sync"
    _wire_app(session_id)
    client = TestClient(app)

    with client.websocket_connect(f"/v1/sessions/{session_id}/ws?token=dev_token") as ws:
        sync = ws.receive_json()
        assert sync["type"] == "sync"
        assert sync["last_seq"] == 0
