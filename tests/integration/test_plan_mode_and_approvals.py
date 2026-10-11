"""Integration test for plan-mode enforcement and the human approval gate over WebSocket.

Covers the full path: orchestrator emits ``approval_required`` → API maps
it to ``EventType.APPROVAL_REQUIRED`` → the cockpit receives it → the user approves
over the WebSocket → the paused turn resumes.
"""

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


class ScriptedGateway(LLMGatewayProtocol):
    """Replays a fixed script of tool calls, then a final textual answer."""

    def __init__(self, script: list[dict[str, Any]], final: str = "Done.") -> None:
        self.script = script
        self.final = final
        self.step = 0

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        if self.step < len(self.script):
            call = self.script[self.step]
            self.step += 1
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call_{self.step}",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("arguments", {})),
                    },
                }
            )
            return
        yield StreamChunk(text_delta=self.final)

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
        pass


def _wire_app(session_id: str, gateway: LLMGatewayProtocol) -> None:
    """Install a scripted orchestrator, sandbox stub and stores on the FastAPI app."""
    driver = MagicMock(spec=SandboxDriverProtocol)
    driver.ensure_workspace = AsyncMock()
    driver.start_sandbox = AsyncMock()
    driver.exec_command = AsyncMock()
    driver.read_file = AsyncMock(return_value="content")

    registry = ToolRegistry(driver=driver, session_id=session_id)
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)
    store = InMemorySessionStore()
    store._sessions[session_id] = SessionRecord(
        session_id=session_id,
        tenant_org_id="default_org",
        tenant_user_id="dev_user",
        title="Approval Test",
    )

    app.state.sandbox_driver = driver
    app.state.llm_gateway = gateway
    app.state.tool_registry = registry
    app.state.orchestrator = orchestrator
    app.state.event_bus = AsyncIOEventBus()
    app.state.session_store = store


def _read_until(ws: Any, predicate: Any, collected: list[dict[str, Any]]) -> None:
    """Collect WebSocket frames until ``predicate`` matches."""
    while True:
        msg = ws.receive_json()
        collected.append(msg)
        if predicate(msg):
            return


def _completed(message: dict[str, Any]) -> bool:
    return (
        message.get("event_type") == "status_changed"
        and message.get("payload", {}).get("status") == "completed"
    )


@pytest.mark.asyncio
async def test_approval_required_event_and_resume() -> None:
    """A request_approval tool call surfaces approval_required and resumes on approval."""
    session_id = f"approval_{uuid.uuid4().hex[:8]}"
    gateway = ScriptedGateway(
        script=[
            {
                "name": "request_approval",
                "arguments": {
                    "action_type": "git_revert_hard",
                    "description": "Discard uncommitted work",
                    "risk_level": "critical",
                },
            }
        ],
        final="Proceeding with the approved action.",
    )
    _wire_app(session_id, gateway)
    client = TestClient(app)

    with client.websocket_connect(f"/v1/sessions/{session_id}/ws?token=dev_token") as ws:
        assert ws.receive_json()["type"] == "sync"
        ws.send_json({"type": "turn", "content": "Reset the workspace"})

        collected: list[dict[str, Any]] = []
        _read_until(ws, lambda m: m.get("event_type") == "approval_required", collected)

        approval_event = next(m for m in collected if m.get("event_type") == "approval_required")
        assert approval_event["payload"]["action_type"] == "git_revert_hard"
        assert approval_event["payload"]["risk_level"] == "critical"
        action_id = approval_event["payload"]["action_id"]
        assert action_id

        # Approve over the WebSocket, mirroring the cockpit ApprovalCard.
        ws.send_json({"type": "approval", "action_id": action_id, "approved": True})

        collected.clear()
        _read_until(ws, _completed, collected)

        tool_completed = next(m for m in collected if m.get("event_type") == "tool_completed")
        assert tool_completed["payload"]["status"] == "completed"
        assert "Approved" in tool_completed["payload"]["result"]


@pytest.mark.asyncio
async def test_plan_mode_blocks_mutating_tools() -> None:
    """While plan mode is active, mutating tools are rejected server-side."""
    session_id = f"planmode_{uuid.uuid4().hex[:8]}"
    gateway = ScriptedGateway(
        script=[
            {"name": "enter_plan_mode", "arguments": {}},
            {"name": "bash_exec", "arguments": {"command": "rm -rf /tmp/danger"}},
        ],
        final="Plan drafted.",
    )
    _wire_app(session_id, gateway)
    client = TestClient(app)

    with client.websocket_connect(f"/v1/sessions/{session_id}/ws?token=dev_token") as ws:
        assert ws.receive_json()["type"] == "sync"
        ws.send_json({"type": "turn", "content": "Refactor the auth module"})

        collected: list[dict[str, Any]] = []
        _read_until(ws, _completed, collected)

        blocked = next(
            m
            for m in collected
            if m.get("event_type") == "tool_completed" and m["payload"]["name"] == "bash_exec"
        )
        assert "Blocked" in blocked["payload"]["result"]
        # The mutating command must never have reached the sandbox.
        assert not app.state.sandbox_driver.exec_command.called


@pytest.mark.asyncio
async def test_denied_approval_instructs_the_agent_to_stop() -> None:
    """A rejected approval returns an explicit denial to the model."""
    session_id = f"denied_{uuid.uuid4().hex[:8]}"
    gateway = ScriptedGateway(
        script=[
            {
                "name": "request_approval",
                "arguments": {
                    "action_type": "force_push",
                    "description": "Overwrite the remote branch",
                },
            }
        ],
        final="Understood, I will not push.",
    )
    _wire_app(session_id, gateway)
    client = TestClient(app)

    with client.websocket_connect(f"/v1/sessions/{session_id}/ws?token=dev_token") as ws:
        assert ws.receive_json()["type"] == "sync"
        ws.send_json({"type": "turn", "content": "Push the branch"})

        collected: list[dict[str, Any]] = []
        _read_until(ws, lambda m: m.get("event_type") == "approval_required", collected)
        action_id = next(m for m in collected if m.get("event_type") == "approval_required")[
            "payload"
        ]["action_id"]

        ws.send_json({"type": "approval", "action_id": action_id, "approved": False})

        collected.clear()
        _read_until(ws, _completed, collected)

        tool_completed = next(m for m in collected if m.get("event_type") == "tool_completed")
        assert "Denied" in tool_completed["payload"]["result"]
        assert "do NOT perform" in tool_completed["payload"]["result"]
