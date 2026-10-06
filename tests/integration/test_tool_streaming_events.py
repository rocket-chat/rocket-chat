"""Integration test verifying end-to-end tool streaming events over WebSocket."""

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
async def test_tool_started_and_completed_streaming() -> None:
    session_id = f"tool_stream_{uuid.uuid4().hex[:8]}"

    driver = MagicMock(spec=SandboxDriverProtocol)
    driver.ensure_workspace = AsyncMock()
    driver.start_sandbox = AsyncMock()
    driver.read_file = AsyncMock(return_value="print('hello from sandbox')")

    class ToolTestGateway(LLMGatewayProtocol):
        def __init__(self) -> None:
            self.turn_step = 0

        async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
            self.turn_step += 1
            if self.turn_step == 1:
                yield StreamChunk(reasoning_delta="Inspecting code file...")
                yield StreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call_inspect_1",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps({"path": "test.py"}),
                        },
                    }
                )
            else:
                yield StreamChunk(text_delta="File read successfully.")

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

    gateway = ToolTestGateway()
    registry = ToolRegistry(driver=driver, session_id=session_id)
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)
    event_bus = AsyncIOEventBus()
    session_store = InMemorySessionStore()
    session_store._sessions[session_id] = SessionRecord(
        session_id=session_id,
        tenant_org_id="default_org",
        tenant_user_id="dev_user",
        title="Streaming Tool Test",
    )

    app.state.sandbox_driver = driver
    app.state.llm_gateway = gateway
    app.state.tool_registry = registry
    app.state.orchestrator = orchestrator
    app.state.event_bus = event_bus
    app.state.session_store = session_store

    client = TestClient(app)

    with client.websocket_connect(f"/v1/sessions/{session_id}/ws?token=dev_token") as ws:
        sync_msg = ws.receive_json()
        assert sync_msg["type"] == "sync"

        # Dispatch turn with custom model override
        ws.send_json(
            {
                "type": "turn",
                "content": "Check test.py",
                "model": "openrouter/deepseek/deepseek-v4.1-flash",
            }
        )

        events_received: list[dict[str, Any]] = []
        while True:
            msg = ws.receive_json()
            events_received.append(msg)
            if (
                msg.get("event_type") == "status_changed"
                and msg.get("payload", {}).get("status") == "completed"
            ):
                break

        event_types = [e.get("event_type") for e in events_received]
        assert "tool_started" in event_types
        assert "tool_completed" in event_types

        tool_started = next(e for e in events_received if e.get("event_type") == "tool_started")
        assert tool_started["payload"]["name"] == "file_read"
        assert tool_started["payload"]["arguments"]["path"] == "test.py"

        tool_completed = next(e for e in events_received if e.get("event_type") == "tool_completed")
        assert tool_completed["payload"]["name"] == "file_read"
        assert tool_completed["payload"]["status"] == "completed"
        assert "duration_ms" in tool_completed["payload"]
        assert "print('hello from sandbox')" in tool_completed["payload"]["result"]
