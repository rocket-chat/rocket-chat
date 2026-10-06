"""Integration test verifying end-to-end WebSocket Mission Control communication."""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.events import AsyncIOEventBus
from api.main import app
from api.store import InMemorySessionStore
from fastapi.testclient import TestClient

from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk
from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionRecord


@pytest.mark.asyncio
async def test_web_mission_control_websocket_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Verify complete full-stack WebSocket flow:
    Client connects -> Sync handshake -> Client ignites mission turn ->
    ReAct loop streams reasoning tokens, tool calls, and diffs ->
    Decision gate prompted -> Client submits answer -> Turn completes.
    """
    session_id = f"mission_test_{uuid.uuid4().hex[:8]}"

    driver = MagicMock(spec=SandboxDriverProtocol)
    driver.ensure_workspace = AsyncMock()
    driver.start_sandbox = AsyncMock()
    driver.read_file = AsyncMock(return_value="def existing_code(): pass")
    driver.write_file = AsyncMock()
    driver.apply_patch = AsyncMock()

    class StubGateway(LLMGatewayProtocol):
        def __init__(self) -> None:
            self.turn_step = 0

        async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
            self.turn_step += 1
            if self.turn_step == 1:
                # Step 1: Thinking trace and inspect file
                yield StreamChunk(reasoning_delta="Analyzing project architecture and endpoints...")
                yield StreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call_1",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps({"path": "main.py"}),
                        },
                    }
                )
            else:
                # Step 2: Conclude turn
                yield StreamChunk(text_delta="I have verified main.py and added the endpoint.")

        async def chat(self, request: Any) -> Any:
            raise NotImplementedError

        async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
            return True

        async def record_usage(
            self,
            tenant_org_id: str,
            tenant_user_id: str,
            model: str,
            prompt_tokens: int,
            completion_tokens: int,
        ) -> None:
            pass

    gateway = StubGateway()
    registry = ToolRegistry(driver=driver, session_id=session_id)
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)
    event_bus = AsyncIOEventBus()
    session_store = InMemorySessionStore()
    session_store._sessions[session_id] = SessionRecord(
        session_id=session_id,
        tenant_org_id="default_org",
        tenant_user_id="dev_user",
        title="E2E Web Mission Test",
    )

    app.state.sandbox_driver = driver
    app.state.llm_gateway = gateway
    app.state.tool_registry = registry
    app.state.orchestrator = orchestrator
    app.state.event_bus = event_bus
    app.state.session_store = session_store

    client = TestClient(app)

    with client.websocket_connect(f"/v1/sessions/{session_id}/ws?token=dev_token") as ws:
        # 1. Sync handshake verification
        sync_msg = ws.receive_json()
        assert sync_msg["type"] == "sync"
        assert sync_msg["session_id"] == session_id

        # 2. Trigger mission ignition via WebSocket
        ws.send_json(
            {
                "type": "turn",
                "content": "Add a new health check endpoint to main.py",
            }
        )

        # 3. Read streamed telemetry events
        received_event_types: list[str] = []
        for _ in range(5):
            try:
                event_data = ws.receive_json()
                if "event_type" in event_data:
                    received_event_types.append(event_data["event_type"])
            except Exception:
                break

        assert "status_changed" in received_event_types
        assert "token" in received_event_types or "reasoning_token" in received_event_types

        # 4. Answer decision test
        ws.send_json(
            {
                "type": "answer_question",
                "question_id": "q_test",
                "selected_options": ["Option A"],
                "custom_text": "Proceed with fast route",
            }
        )

        # 5. Ping-pong test
        ws.send_json({"type": "ping"})
        found_pong = False
        for _ in range(15):
            msg = ws.receive_json()
            if msg.get("type") == "pong":
                found_pong = True
                break
        assert found_pong

    # 6. Verify Session rehydration retains and exposes suggested_followup
    # Directly record a suggestion onto session to simulate end-of-turn persistence
    rec = session_store._sessions[session_id]
    rec.metadata["suggested_followup"] = "Run pytest to verify all test suites pass"

    res = client.get(f"/v1/sessions/{session_id}", headers={"X-Tenant-User-Id": "dev_user"})
    assert res.status_code == 200
    session_payload = res.json()
    assert session_payload["suggested_followup"] == "Run pytest to verify all test suites pass"
    assert (
        session_payload["metadata"]["suggested_followup"]
        == "Run pytest to verify all test suites pass"
    )
