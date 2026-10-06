"""End-to-end integration tests for parallel tool execution, subagent delegation, and write-only MCP secrets."""

from __future__ import annotations

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

from specifications.interfaces.llm import LLMGatewayProtocol, ModelRequest, StreamChunk
from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionRecord


@pytest.mark.asyncio
async def test_e2e_parallel_tool_streaming_over_websocket() -> None:
    """Verifies that multiple tool calls emitted in a single turn execute and stream concurrently over WebSocket."""
    session_id = f"e2e_parallel_{uuid.uuid4().hex[:8]}"

    driver = MagicMock(spec=SandboxDriverProtocol)
    driver.ensure_workspace = AsyncMock()
    driver.start_sandbox = AsyncMock()
    driver.read_file = AsyncMock(side_effect=lambda sid, path: f"content of {path}")

    class ParallelToolGateway(LLMGatewayProtocol):
        def __init__(self) -> None:
            self.turn = 0

        async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
            self.turn += 1
            if self.turn == 1:
                # Emit 3 parallel tool calls in one turn
                yield StreamChunk(reasoning_delta="Inspecting three files concurrently...")
                for idx, path in enumerate(["a.py", "b.py", "c.py"]):
                    yield StreamChunk(
                        tool_call_delta={
                            "index": idx,
                            "id": f"call_{idx + 1}",
                            "function": {
                                "name": "file_read",
                                "arguments": json.dumps({"path": path}),
                            },
                        }
                    )
            else:
                yield StreamChunk(text_delta="Parallel inspection finished successfully.")

        async def chat(self, request: Any) -> Any:
            raise NotImplementedError

        async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
            return True

        async def record_usage(
            self, tenant_org_id: str, tenant_user_id: str, model: str, pt: int, ct: int
        ) -> None:
            pass

    gateway = ParallelToolGateway()
    registry = ToolRegistry(driver=driver, session_id=session_id)
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)
    event_bus = AsyncIOEventBus()
    session_store = InMemorySessionStore()
    session_store._sessions[session_id] = SessionRecord(
        session_id=session_id,
        tenant_org_id="org_default",
        tenant_user_id="user_e2e",
        title="Parallel Tool E2E Test",
    )

    app.state.sandbox_driver = driver
    app.state.llm_gateway = gateway
    app.state.tool_registry = registry
    app.state.orchestrator = orchestrator
    app.state.event_bus = event_bus
    app.state.session_store = session_store

    client = TestClient(app)

    with client.websocket_connect(f"/v1/sessions/{session_id}/ws?token=dev_token") as ws:
        # Handshake
        sync_msg = ws.receive_json()
        assert sync_msg["type"] == "sync"

        # Ignite turn
        ws.send_json({"type": "turn", "content": "Read a.py, b.py, and c.py concurrently"})

        started_tools: list[str] = []
        completed_tools: list[str] = []

        # Consume WebSocket stream frames
        for _ in range(20):
            try:
                frame = ws.receive_json()
                ev_type = frame.get("event_type")
                payload = frame.get("payload", {})
                if ev_type == "tool_started":
                    started_tools.append(payload.get("id"))
                elif ev_type == "tool_completed":
                    completed_tools.append(payload.get("id"))
                elif ev_type == "status_changed" and payload.get("status") == "completed":
                    break
            except Exception:
                break

        # Verify all 3 parallel tools were triggered and completed
        assert len(started_tools) == 3
        assert len(completed_tools) == 3
        assert set(started_tools) == {"call_1", "call_2", "call_3"}
        assert set(completed_tools) == {"call_1", "call_2", "call_3"}


@pytest.mark.asyncio
async def test_e2e_subagent_delegation_streaming_over_websocket() -> None:
    """Verifies end-to-end WebSocket telemetry for subagent task delegation."""
    session_id = f"e2e_subagent_{uuid.uuid4().hex[:8]}"

    driver = MagicMock(spec=SandboxDriverProtocol)
    driver.ensure_workspace = AsyncMock()
    driver.start_sandbox = AsyncMock()
    driver.read_file = AsyncMock(return_value="def authenticate(): pass")

    class SubagentDelegationGateway(LLMGatewayProtocol):
        def __init__(self) -> None:
            self.calls = 0

        async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
            self.calls += 1
            is_subagent = any(
                "Security Auditor" in (m.content or "")
                for m in request.messages
                if m.role == "system"
            )

            if is_subagent:
                # Subagent execution
                yield StreamChunk(reasoning_delta="Auditing JWT signing algorithms...")
                yield StreamChunk(text_delta="Security Audit Complete: Alg 'none' is disallowed.")
            else:
                if self.calls == 1:
                    # Main agent delegates to security_auditor
                    yield StreamChunk(
                        tool_call_delta={
                            "index": 0,
                            "id": "sub_call_1",
                            "function": {
                                "name": "delegate_subagent",
                                "arguments": json.dumps(
                                    {
                                        "role": "security_auditor",
                                        "task": "Audit authentication security",
                                        "context": "Check app/auth.py",
                                    }
                                ),
                            },
                        }
                    )
                else:
                    yield StreamChunk(text_delta="Security review finalized; patch verified.")

        async def chat(self, request: Any) -> Any:
            raise NotImplementedError

        async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
            return True

        async def record_usage(
            self, tenant_org_id: str, tenant_user_id: str, model: str, pt: int, ct: int
        ) -> None:
            pass

    gateway = SubagentDelegationGateway()
    registry = ToolRegistry(driver=driver, session_id=session_id)
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)
    event_bus = AsyncIOEventBus()
    session_store = InMemorySessionStore()
    session_store._sessions[session_id] = SessionRecord(
        session_id=session_id,
        tenant_org_id="org_default",
        tenant_user_id="user_e2e",
        title="Subagent E2E Test",
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

        ws.send_json({"type": "turn", "content": "Run security audit on auth module"})

        received_events: list[str] = []
        subagent_events: dict[str, Any] = {}

        for _ in range(25):
            try:
                frame = ws.receive_json()
                ev_type = frame.get("event_type")
                if ev_type:
                    received_events.append(ev_type)
                    if ev_type.startswith("subagent_"):
                        subagent_events[ev_type] = frame.get("payload", {})
                if (
                    ev_type == "status_changed"
                    and frame.get("payload", {}).get("status") == "completed"
                ):
                    break
            except Exception:
                break

        # Verify WebSocket received proper subagent lifecycle events
        assert "subagent_started" in received_events
        assert "subagent_completed" in received_events
        assert subagent_events["subagent_started"]["role"] == "security_auditor"
        assert "Security Audit Complete" in subagent_events["subagent_completed"]["summary"]


@pytest.mark.asyncio
async def test_e2e_mcp_multi_scheme_write_only_secrets() -> None:
    """Verifies full lifecycle of multi-scheme MCP server registration with write-only secrets."""
    client = TestClient(app)

    # 1. Register MCP server with HTTP scheme and write-only credentials
    payload = {
        "name": "E2E Sentry Inspector",
        "transport": "http",
        "endpoint_or_command": "https://sentry.corp.internal/mcp",
        "guidance": "Use this MCP server to fetch tracebacks for runtime crashes.",
        "auth": {
            "auth_type": "bearer",
            "api_key": "sntry_live_11223344556677889900aabbccddeeff",
            "header_name": "Authorization",
            "header_prefix": "Bearer",
            "headers": [
                {
                    "name": "X-Secret-Auth-Token",
                    "value": "super-private-token-value-never-expose",
                    "is_secret": True,
                },
                {
                    "name": "X-Public-Client",
                    "value": "web-dashboard-v1",
                    "is_secret": False,
                },
            ],
        },
    }

    create_res = client.post(
        "/v1/mcp/servers",
        json=payload,
        headers={"Authorization": "Bearer dev_token"},
    )
    assert create_res.status_code == 200
    server_data = create_res.json()

    # Verify write-only guarantee
    raw_response_text = create_res.text
    assert "sntry_live_11223344556677889900aabbccddeeff" not in raw_response_text
    assert "super-private-token-value-never-expose" not in raw_response_text
    assert server_data["has_api_key"] is True
    assert "eeff" in (server_data["api_key_fingerprint"] or "")

    server_id = server_data["id"]

    # 2. List servers and verify masked projection
    list_res = client.get("/v1/mcp/servers", headers={"Authorization": "Bearer dev_token"})
    assert list_res.status_code == 200
    all_servers = list_res.json()
    assert any(s["id"] == server_id for s in all_servers)
    assert "super-private-token-value-never-expose" not in list_res.text

    # 3. Probe connection
    probe_res = client.post(
        f"/v1/mcp/servers/{server_id}/probe",
        headers={"Authorization": "Bearer dev_token"},
    )
    assert probe_res.status_code == 200
    assert probe_res.json()["status"] == "connected"

    # 4. Clean up
    del_res = client.delete(
        f"/v1/mcp/servers/{server_id}",
        headers={"Authorization": "Bearer dev_token"},
    )
    assert del_res.status_code == 200


@pytest.mark.asyncio
async def test_e2e_subagents_settings_rest_crud() -> None:
    """Verifies REST API management for specialist subagents."""
    client = TestClient(app)

    # 1. Fetch available subagents
    res = client.get("/v1/subagents", headers={"Authorization": "Bearer dev_token"})
    assert res.status_code == 200
    subagents = res.json()
    assert len(subagents) >= 3

    qa_agent = next(s for s in subagents if s["id"] == "qa_verifier")
    assert qa_agent["max_turns"] == 4

    # 2. Update QA Verifier settings
    qa_agent["max_turns"] = 5
    qa_agent["temperature"] = 0.05
    qa_agent["system_prompt"] = "Synthesize pytest and Playwright tests rigorously."

    update_res = client.put(
        "/v1/subagents/qa_verifier",
        json=qa_agent,
        headers={"Authorization": "Bearer dev_token"},
    )
    assert update_res.status_code == 200
    updated = update_res.json()
    assert updated["max_turns"] == 5
    assert updated["temperature"] == 0.05
    assert "Playwright" in updated["system_prompt"]
