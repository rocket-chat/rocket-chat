"""Permission modes: what waits for a human, enforced before the tool runs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.permissions import approval_needed
from agent_core.tools.registry import ToolRegistry
from api.services.turn_service import resolve_permission_mode
from fastapi.testclient import TestClient
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import AgentEvent, TurnPolicy
from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk
from specifications.interfaces.sandbox import WorkspaceSpec

# --- classification ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode, tool, arguments, expected_risk",
    [
        ("ask", "file_write", {"path": "a.py"}, "medium"),
        ("ask", "bash_exec", {"command": "make"}, "medium"),
        ("ask", "git_open_pr", {"title": "x"}, "high"),
        ("ask", "git_revert", {"reference": "HEAD~1", "mode": "hard"}, "critical"),
        ("ask", "some_server__tool", {}, "medium"),
        ("auto_edit", "bash_exec", {"command": "make"}, "medium"),
        ("auto_edit", "git_commit", {"message": "m"}, "high"),
    ],
)
def test_gated_calls_carry_their_risk(
    mode: str, tool: str, arguments: dict[str, Any], expected_risk: str
) -> None:
    need = approval_needed(mode, tool, arguments)
    assert need is not None
    assert (need.action_type, need.risk_level) == (tool, expected_risk)


@pytest.mark.parametrize(
    "mode, tool",
    [
        ("ask", "file_read"),
        ("ask", "grep_search"),
        ("ask", "think"),
        ("auto_edit", "file_write"),
        ("auto_edit", "file_edit"),
        ("auto", "bash_exec"),
        ("auto", "git_open_pr"),
        ("bypass", "bash_exec"),
        ("plan", "file_write"),  # plan mode blocks by its own mechanism, not by a prompt
    ],
)
def test_ungated_calls_run_without_a_prompt(mode: str, tool: str) -> None:
    assert approval_needed(mode, tool, {}) is None


def test_the_prompt_describes_the_call_and_is_bounded() -> None:
    need = approval_needed("ask", "bash_exec", {"command": "x" * 5000})
    assert need is not None
    assert need.description.startswith("bash_exec: xxx")
    assert len(need.description) <= 500


# --- resolution --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "session, preferred, locked, allow_bypass, expected",
    [
        (None, "auto", False, False, "auto"),
        (None, "ask", False, False, "ask"),
        ("plan", "ask", False, False, "plan"),
        ("auto", "ask", True, False, "ask"),  # an organization lock beats the session
        ("bypass", "auto", False, False, "ask"),
        ("bypass", "auto", False, True, "bypass"),
        (None, "bypass", False, False, "ask"),
        (None, "bypass", True, True, "bypass"),
        ("nonsense", "ask", False, False, "ask"),
        (None, "", False, False, "auto"),
    ],
)
def test_mode_resolution_prefers_the_most_specific_and_never_loosens_by_default(
    session: str | None, preferred: str, locked: bool, allow_bypass: bool, expected: str
) -> None:
    assert (
        resolve_permission_mode(session, preferred, locked=locked, allow_bypass=allow_bypass)
        == expected
    )


# --- enforcement through the orchestrator -------------------------------------------------


class _OneToolGateway(LLMGatewayProtocol):
    """Calls a single tool, then answers."""

    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.name, self.arguments, self.calls = name, arguments, 0

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        self.calls += 1
        if self.calls == 1:
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": self.name, "arguments": json.dumps(self.arguments)},
                }
            )
            return
        yield StreamChunk(text_delta="done")

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


async def _run_turn(
    mode: str, tool: str, arguments: dict[str, Any], *, approve: bool | None
) -> tuple[list[AgentEvent], FakeSandboxDriver]:
    """Drive one turn; ``approve`` answers any approval gate (None = fail if one is raised)."""
    driver = FakeSandboxDriver()
    await driver.ensure_workspace(
        WorkspaceSpec(session_id="s1", tenant_org_id="o", tenant_user_id="u", container_image="img")
    )
    await driver.start_sandbox("s1")
    registry = ToolRegistry(driver=driver, session_id="s1")
    orchestrator = AsyncReActOrchestrator(
        gateway=_OneToolGateway(tool, arguments), registry=registry
    )
    policy = replace(TurnPolicy(), permission_mode=mode)
    events: list[AgentEvent] = []
    async with asyncio.timeout(20):
        async for event in orchestrator.process_user_turn("s1", "go", policy=policy):
            events.append(event)
            if event.event_type == "approval_required":
                assert approve is not None, "no approval gate expected in this mode"
                await orchestrator.submit_human_approval(
                    session_id="s1", action_id=event.payload["action_id"], approved=approve
                )
    return events, driver


def _gates(events: list[AgentEvent]) -> list[dict[str, Any]]:
    return [e.payload for e in events if e.event_type == "approval_required"]


def _tool_result(events: list[AgentEvent]) -> str:
    return next(str(e.payload["result"]) for e in events if e.event_type == "tool_completed")


async def test_ask_mode_gates_a_command_and_runs_it_once_approved() -> None:
    events, driver = await _run_turn("ask", "bash_exec", {"command": "echo hi"}, approve=True)
    (gate,) = _gates(events)
    assert (gate["action_type"], gate["risk_level"]) == ("bash_exec", "medium")
    assert "echo hi" in gate["command_or_path"]
    assert [command for _, command in driver.exec_calls if "echo hi" in command]


async def test_a_denied_gate_means_the_tool_never_runs() -> None:
    events, driver = await _run_turn("ask", "bash_exec", {"command": "rm -rf build"}, approve=False)
    assert len(_gates(events)) == 1
    assert "Denied by the user" in _tool_result(events)
    assert not [command for _, command in driver.exec_calls if "rm -rf" in command]


async def test_ask_mode_does_not_interrupt_reading() -> None:
    events, _ = await _run_turn("ask", "list_dir", {"path": "."}, approve=None)
    assert _gates(events) == []


async def test_auto_edit_lets_edits_through_but_gates_commands() -> None:
    edit_events, _ = await _run_turn(
        "auto_edit", "file_write", {"path": "a.txt", "content": "x"}, approve=None
    )
    assert _gates(edit_events) == []
    command_events, _ = await _run_turn("auto_edit", "bash_exec", {"command": "ls"}, approve=True)
    assert len(_gates(command_events)) == 1


async def test_plan_mode_refuses_changes_without_asking() -> None:
    events, driver = await _run_turn(
        "plan", "file_write", {"path": "a.txt", "content": "x"}, approve=None
    )
    assert "plan mode" in _tool_result(events)
    assert _gates(events) == []
    assert driver.exec_calls == []


async def test_a_mode_that_needs_a_human_fails_closed_without_an_approval_channel() -> None:
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id="s1")
    from agent_core.context import ExecutionContext, current_execution_ctx

    token = current_execution_ctx.set(
        ExecutionContext(
            session_id="s1",
            event_queue=asyncio.Queue(),
            parent_model="m",
            policy=replace(TurnPolicy(), permission_mode="ask"),
        )
    )
    try:
        result = await registry.call_tool("bash_exec", {"command": "ls"})
    finally:
        current_execution_ctx.reset(token)
    assert result.startswith("Blocked") and "approval channel" in result


# --- API ---------------------------------------------------------------------------------

OWNER = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "owner_user",
    "X-User-Role": "developer",
}
COLLABORATOR = {**OWNER, "X-Tenant-User-Id": "collab_user"}


def test_only_the_owner_sets_a_valid_permission_mode(client: TestClient) -> None:
    created = client.post("/v1/sessions", json={"title": "modes"}, headers=OWNER).json()
    session_id = created["session_id"]
    client.patch(
        f"/v1/sessions/{session_id}",
        json={"is_shared": True},
        headers=OWNER,
    )

    patch = lambda headers, mode: client.patch(  # noqa: E731
        f"/v1/sessions/{session_id}", json={"metadata": {"permission_mode": mode}}, headers=headers
    )
    assert patch(OWNER, "ask").status_code == 200
    assert patch(OWNER, "yolo").status_code == 422
    assert patch(COLLABORATOR, "bypass").status_code == 403
    stored = client.get(f"/v1/sessions/{session_id}", headers=OWNER).json()
    assert stored["metadata"]["permission_mode"] == "ask"


# --- server-side policy through a full turn ----------------------------------------------


async def _turn_with_org_settings(
    governance: dict[str, Any],
    session_mode: str | None,
    tool: str,
    arguments: dict[str, Any],
    *,
    preferences: dict[str, Any] | None = None,
    locked: list[str] | None = None,
) -> tuple[list[Any], int]:
    """Run a whole web turn (events published on the bus); return (approval events, tool runs)."""
    from api.events import AsyncIOEventBus
    from api.services.settings import save_org_config
    from api.store import InMemorySessionStore
    from api.turns.runner import execute_turn_background

    from specifications.interfaces.events import EventType
    from specifications.interfaces.session import SessionRecord

    org = "perm_org"
    await save_org_config(None, org, "governance", governance, [])
    await save_org_config(None, org, "user_preferences", preferences or {}, locked or [])
    store = InMemorySessionStore()
    metadata = {"permission_mode": session_mode} if session_mode else {}
    await store.create_session(
        SessionRecord(
            session_id="s_perm", tenant_org_id=org, tenant_user_id="u", title="t", metadata=metadata
        )
    )
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id="s_perm")
    orchestrator = AsyncReActOrchestrator(
        gateway=_OneToolGateway(tool, arguments), registry=registry
    )
    bus = AsyncIOEventBus()

    async def answer_humans() -> None:
        seen = 0
        while True:
            events, _ = bus.replay_since("s_perm", 0)
            gates = [e for e in events if e.event_type is EventType.APPROVAL_REQUIRED]
            if len(gates) > seen and gates[-1].payload.get("policy_decision") == "require_human":
                seen = len(gates)
                await orchestrator.submit_human_approval(
                    session_id="s_perm", action_id=gates[-1].payload["action_id"], approved=True
                )
            await asyncio.sleep(0.01)

    helper = asyncio.create_task(answer_humans())
    try:
        async with asyncio.timeout(20):
            await execute_turn_background(
                session_id="s_perm",
                prompt="go",
                orchestrator=orchestrator,
                event_bus=bus,
                driver=driver,
                registry=registry,
                store=store,
            )
    finally:
        helper.cancel()
    events, _ = bus.replay_since("s_perm", 0)
    gates = [e.payload for e in events if e.event_type is EventType.APPROVAL_REQUIRED]
    return gates, len(driver.exec_calls)


async def test_bypass_is_honoured_only_where_the_organization_allows_it() -> None:
    allowed, _ = await _turn_with_org_settings(
        {"allow_bypass_permissions": True},
        "bypass",
        "request_approval",
        {"action_type": "deploy", "description": "ship", "risk_level": "high"},
    )
    assert [g["policy_decision"] for g in allowed] == ["auto_approve"]

    refused, _ = await _turn_with_org_settings(
        {"allow_bypass_permissions": False}, "bypass", "bash_exec", {"command": "ls"}
    )
    # Clamped to ask: the command is gated by a human instead of silently running.
    assert [g["policy_decision"] for g in refused] == ["require_human"]


async def test_the_organization_deny_list_still_wins_in_bypass() -> None:
    gates, _ = await _turn_with_org_settings(
        {"allow_bypass_permissions": True, "approval_policy": {"deny": ["critical"]}},
        "bypass",
        "request_approval",
        {"action_type": "wipe", "description": "drop everything", "risk_level": "critical"},
    )
    assert [g["policy_decision"] for g in gates] == ["deny"]


async def test_the_organization_default_mode_applies_when_the_session_has_none() -> None:
    gates, _ = await _turn_with_org_settings(
        {}, None, "bash_exec", {"command": "ls"}, preferences={"permission_mode": "ask"}
    )
    assert [g["action_type"] for g in gates] == ["bash_exec"]


def test_a_client_cannot_enable_server_only_session_capabilities_at_creation(
    client: TestClient,
) -> None:
    created = client.post(
        "/v1/sessions",
        json={
            "title": "sneaky",
            "metadata": {
                "toolsets": ["slack"],
                "forked_from": "someone-elses-session",
                "title_source": "user",
                "selected_model": "m",
            },
        },
        headers=OWNER,
    ).json()
    stored = client.get(f"/v1/sessions/{created['session_id']}", headers=OWNER).json()["metadata"]
    assert "toolsets" not in stored and "forked_from" not in stored
    assert stored["selected_model"] == "m"


def test_a_session_can_start_in_a_chosen_mode_and_rejects_an_unknown_one(
    client: TestClient,
) -> None:
    created = client.post(
        "/v1/sessions", json={"title": "planned", "permission_mode": "plan"}, headers=OWNER
    ).json()
    stored = client.get(f"/v1/sessions/{created['session_id']}", headers=OWNER).json()["metadata"]
    assert stored["permission_mode"] == "plan"
    rejected = client.post("/v1/sessions", json={"permission_mode": "yolo"}, headers=OWNER)
    assert rejected.status_code == 422


async def test_a_locked_organization_mode_cannot_be_loosened_by_the_session() -> None:
    gates, _ = await _turn_with_org_settings(
        {},
        "auto",
        "bash_exec",
        {"command": "ls"},
        preferences={"permission_mode": "ask"},
        locked=["permission_mode"],
    )
    assert [g["action_type"] for g in gates] == ["bash_exec"]
