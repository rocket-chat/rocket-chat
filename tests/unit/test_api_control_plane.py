"""Unit tests for FastAPI Control Plane REST and WebSocket endpoints."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.events import AsyncIOEventBus
from api.main import app
from api.store import InMemorySessionStore
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from specifications.interfaces.llm import LLMGatewayProtocol
from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionRecord


@pytest.fixture
def mock_app_state() -> None:
    """Prepare mock dependencies on app.state for testing."""
    driver = MagicMock(spec=SandboxDriverProtocol)
    driver.ensure_workspace = AsyncMock()
    driver.start_sandbox = AsyncMock()

    gateway = MagicMock(spec=LLMGatewayProtocol)
    registry = ToolRegistry()
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)
    orchestrator.submit_question_answer = AsyncMock()  # type: ignore[method-assign]
    orchestrator.submit_human_approval = AsyncMock()  # type: ignore[method-assign]
    orchestrator.cancel_turn = AsyncMock()  # type: ignore[method-assign]

    event_bus = AsyncIOEventBus()
    store = InMemorySessionStore()

    app.state.sandbox_driver = driver
    app.state.llm_gateway = gateway
    app.state.tool_registry = registry
    app.state.orchestrator = orchestrator
    app.state.event_bus = event_bus
    app.state.session_store = store


@pytest.mark.asyncio
async def test_health_endpoints(mock_app_state: None) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/health")
        assert r1.status_code == 200
        assert r1.json() == {"status": "healthy"}

        r2 = await client.get("/v1/health")
        assert r2.status_code == 200
        assert r2.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_session_crud(mock_app_state: None) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create session
        create_res = await client.post(
            "/v1/sessions",
            json={
                "title": "Fix Auth Token Bug",
                "tenant_org_id": "test_org",
                "tenant_user_id": "test_user",
                "container_image": "python:3.12-slim",
                "git_branch": "feat/auth",
            },
        )
        assert create_res.status_code == 200
        session_data = create_res.json()
        session_id = session_data["session_id"]
        assert session_data["title"] == "Fix Auth Token Bug"
        assert session_data["git_branch"] == "feat/auth"

        # List sessions
        list_res = await client.get(
            "/v1/sessions?tenant_org_id=test_org",
            headers={"X-Tenant-User-Id": "test_user"},
        )
        assert list_res.status_code == 200
        sessions = list_res.json()
        assert len(sessions) >= 1
        assert any(s["session_id"] == session_id for s in sessions)

        # Get session
        get_res = await client.get(
            f"/v1/sessions/{session_id}", headers={"X-Tenant-User-Id": "test_user"}
        )
        assert get_res.status_code == 200
        assert get_res.json()["session_id"] == session_id

        # Non-existent session
        missing_res = await client.get("/v1/sessions/session_not_found")
        assert missing_res.status_code == 404


@pytest.mark.asyncio
async def test_session_turn_and_controls(mock_app_state: None) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create session first
        create_res = await client.post(
            "/v1/sessions",
            json={"title": "Mission Session"},
        )
        session_id = create_res.json()["session_id"]

        # Post turn
        turn_res = await client.post(
            f"/v1/sessions/{session_id}/turns",
            json={"prompt": "Run the test suite"},
        )
        assert turn_res.status_code == 200
        assert turn_res.json()["status"] == "started"

        # Answer question
        ans_res = await client.post(
            f"/v1/sessions/{session_id}/questions/q_123",
            json={"selected_options": ["Option A"], "custom_text": "Approved"},
        )
        assert ans_res.status_code == 200
        assert ans_res.json()["status"] == "submitted"
        app.state.orchestrator.submit_question_answer.assert_called_once_with(
            session_id=session_id,
            question_id="q_123",
            selected_options=["Option A"],
            custom_text="Approved",
        )

        # Submit approval
        appr_res = await client.post(
            f"/v1/sessions/{session_id}/approvals/action_789",
            json={"approved": True},
        )
        assert appr_res.status_code == 200
        assert appr_res.json()["status"] == "recorded"
        app.state.orchestrator.submit_human_approval.assert_called_once_with(
            session_id=session_id,
            action_id="action_789",
            approved=True,
        )

        # Cancel turn
        cancel_res = await client.delete(f"/v1/sessions/{session_id}/turns")
        assert cancel_res.status_code == 200
        assert cancel_res.json()["status"] == "cancelled"
        app.state.orchestrator.cancel_turn.assert_called_once_with(session_id)


def test_websocket_sync_and_ping(mock_app_state: None) -> None:
    client = TestClient(app)
    session_id = "test_ws_session"
    app.state.session_store._sessions[session_id] = SessionRecord(
        session_id=session_id,
        tenant_org_id="default_org",
        tenant_user_id="dev_user",
        title="Test WS Session",
    )

    with client.websocket_connect(f"/v1/sessions/{session_id}/ws?token=dev_token") as ws:
        # Initial sync message
        sync_msg = ws.receive_json()
        assert sync_msg["type"] == "sync"
        assert sync_msg["session_id"] == session_id

        # Ping-pong test
        ws.send_json({"type": "ping"})
        pong_msg = ws.receive_json()
        assert pong_msg["type"] == "pong"


@pytest.mark.asyncio
async def test_list_models(mock_app_state: None) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/v1/models")
        assert res.status_code == 200
        data = res.json()
        assert "default_model" in data
        assert "models" in data
        assert len(data["models"]) >= 3
        model_ids = [m["id"] for m in data["models"]]
        assert "openrouter/deepseek/deepseek-v4.1-flash" in model_ids


@pytest.mark.asyncio
async def test_session_turn_with_model_override(mock_app_state: None) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_res = await client.post("/v1/sessions", json={"title": "Model Test"})
        session_id = create_res.json()["session_id"]

        turn_res = await client.post(
            f"/v1/sessions/{session_id}/turns",
            json={
                "prompt": "Test with custom model",
                "model": "openrouter/openai/gpt-4o",
            },
        )
        assert turn_res.status_code == 200
        assert turn_res.json()["status"] == "started"


@pytest.mark.asyncio
async def test_agent_personas(mock_app_state: None) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # List default agents
        res = await client.get("/v1/agents")
        assert res.status_code == 200
        agents = res.json()
        assert len(agents) >= 1
        agent_ids = [a["id"] for a in agents]
        assert "persona-general" in agent_ids

        # Create/Update custom agent
        new_agent = {
            "id": "persona-custom-tester",
            "name": "Custom QA Engineer",
            "role_title": "End-to-End QA Automation",
            "description": "Writes Playwright and unit test suites.",
            "system_prompt": "You are a test automation engineer.",
            "model": "openrouter/deepseek/deepseek-v4.1-flash",
            "whitelisted_tools": ["bash_exec", "file_read"],
            "icon": "check",
        }
        create_res = await client.post("/v1/agents", json=new_agent)
        assert create_res.status_code == 200
        assert create_res.json()["name"] == "Custom QA Engineer"

        # Verify listed
        res2 = await client.get("/v1/agents")
        assert res2.status_code == 200
        updated_ids = [a["id"] for a in res2.json()]
        assert "persona-custom-tester" in updated_ids


@pytest.mark.asyncio
async def test_delete_session(mock_app_state: None) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create session with agent_id
        create_res = await client.post(
            "/v1/sessions",
            json={
                "title": "Session To Delete",
                "agent_id": "persona-general",
            },
        )
        assert create_res.status_code == 200
        session_id = create_res.json()["session_id"]
        assert create_res.json()["agent_id"] == "persona-general"

        # Verify session exists
        get_res = await client.get(f"/v1/sessions/{session_id}")
        assert get_res.status_code == 200

        # Delete session
        del_res = await client.delete(f"/v1/sessions/{session_id}")
        assert del_res.status_code == 200
        assert del_res.json()["deleted"] is True

        # Verify session is gone
        missing_res = await client.get(
            f"/v1/sessions/{session_id}", headers={"X-Tenant-User-Id": "test_user"}
        )
        assert missing_res.status_code == 404

        # Verify deleting non-existent session returns 404
        del_missing = await client.delete(f"/v1/sessions/{session_id}")
        assert del_missing.status_code == 404


@pytest.mark.asyncio
async def test_followup_suggestion_event_emission(mock_app_state: None) -> None:
    """Verify follow-up suggestion is published on turn completion without exposing reasoning."""
    from specifications.interfaces.agent import AgentEvent
    from specifications.interfaces.events import EventType

    session_id = "test_sugg_session"
    record = SessionRecord(
        session_id=session_id,
        tenant_org_id="default_org",
        tenant_user_id="dev_user",
        title="Suggestion Test",
    )
    await app.state.session_store.create_session(record)

    events_captured = []
    stop_event = asyncio.Event()

    async def _listener():
        async for evt in app.state.event_bus.subscribe(session_id):
            events_captured.append(evt)
            if stop_event.is_set():
                break

    listen_task = asyncio.create_task(_listener())

    # Mock orchestrator turn emitting thought, reasoning and completion
    async def mock_turn(*args, **kwargs):
        yield AgentEvent(
            event_type="thought",
            session_id=session_id,
            payload={"delta": "Internal reasoning step", "is_reasoning": True},
        )
        yield AgentEvent(
            event_type="thought",
            session_id=session_id,
            payload={"delta": "Code refactored and updated.", "is_reasoning": False},
        )
        yield AgentEvent(
            event_type="status",
            session_id=session_id,
            payload={"status": "completed"},
        )

    app.state.orchestrator.process_user_turn = mock_turn

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            f"/v1/sessions/{session_id}/turns",
            json={"prompt": "Please edit the configuration"},
        )
        assert res.status_code == 200

    # Wait briefly for background turn task and events
    await asyncio.sleep(0.2)
    stop_event.set()
    listen_task.cancel()

    sugg_events = [e for e in events_captured if e.event_type == EventType.FOLLOWUP_SUGGESTION]
    assert len(sugg_events) >= 1
    sugg_text = sugg_events[0].payload["suggestion"]
    assert bool(sugg_text)
    # Ensure internal reasoning scratchpad is never leaked into suggested prompt
    assert "Internal reasoning" not in sugg_text
    assert "<think>" not in sugg_text

    # Verify suggestion is persisted into session record and reloaded in GET /v1/sessions/{session_id}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        sess_get = await client.get(f"/v1/sessions/{session_id}")
        assert sess_get.status_code == 200
        sess_body = sess_get.json()
        assert sess_body.get("suggested_followup") == sugg_text
        assert sess_body.get("metadata", {}).get("suggested_followup") == sugg_text
