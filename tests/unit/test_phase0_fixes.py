"""Unit tests for Phase 0 critical fixes (C1, C2, C3, C4)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.permissions import approval_needed, is_read_call, touches_workspace
from agent_core.tools.registry import ToolRegistry
from api.events import AsyncIOEventBus
from api.services.artifacts import InMemoryArtifactStore
from api.turns.callbacks import _make_artifact_callbacks
from git_engine.publish import BranchPublisher, PublishError, _Plan
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import APPROVAL_GRANTED_PREFIX
from specifications.interfaces.events import EventType
from specifications.interfaces.session import SessionArtifact


@pytest.mark.asyncio
async def test_c1_artifact_callback_event_publish_arity() -> None:
    """C1: Artifact callbacks publish events with single SessionEvent argument (no arity mismatch)."""
    art_store = InMemoryArtifactStore()
    event_bus = AsyncIOEventBus()
    event_bus.publish = AsyncMock(wraps=event_bus.publish)  # type: ignore[method-assign]
    session_id = "sess_c1"

    create_cb, update_cb = _make_artifact_callbacks(art_store, event_bus, session_id)

    art = SessionArtifact(
        id="art_c1",
        session_id=session_id,
        tenant_org_id="org_test",
        title="Test Artifact",
        filename="test.md",
        artifact_type="markdown",
        content="# Hello World",
    )

    # Calling create_cb must not raise TypeError
    saved = await create_cb(art)
    assert saved.id == "art_c1"

    # Verify event was published to the bus with single SessionEvent argument
    event_bus.publish.assert_awaited_once()
    ev = event_bus.publish.call_args[0][0]
    assert ev.event_type == EventType.ARTIFACT_CREATED
    assert ev.session_id == session_id
    assert ev.payload["id"] == "art_c1"

    # Calling update_cb must not raise TypeError
    updated = await update_cb(session_id, "art_c1", "org_test", "# Updated Content")
    assert updated is not None
    assert updated.content == "# Updated Content"

    assert event_bus.publish.await_count == 2
    ev2 = event_bus.publish.call_args[0][0]
    assert ev2.event_type == EventType.ARTIFACT_UPDATED
    assert ev2.payload["version"] == 2


def test_c2_repo_foreach_and_task_kill_classified_as_command() -> None:
    """C2: repo_foreach, task_kill, and process_stop are commands (not reads) and touch workspace."""
    # repo_foreach
    assert not is_read_call("repo_foreach", {"command": "ls"})
    assert approval_needed("ask", "repo_foreach", {"command": "echo 1"}) is not None
    assert approval_needed("auto_edit", "repo_foreach", {"command": "echo 1"}) is not None
    assert touches_workspace("repo_foreach", {"command": "ls"})

    # task_kill and process_stop
    assert not is_read_call("task_kill", {"task_id": "p123"})
    assert approval_needed("ask", "task_kill", {"task_id": "p123"}) is not None
    assert approval_needed("auto_edit", "task_kill", {"task_id": "p123"}) is not None

    assert not is_read_call("process_stop", {"process_id": "p123"})
    assert approval_needed("ask", "process_stop", {"process_id": "p123"}) is not None


@pytest.mark.asyncio
async def test_c3_exit_plan_mode_only_clears_on_approval() -> None:
    """C3: exit_plan_mode does not clear plan mode until approval is actually granted."""
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id="sess_c3")

    ctx = ExecutionContext(
        session_id="sess_c3",
        event_queue=asyncio.Queue(),
        parent_model="test-model",
    )
    ctx.plan_mode = True
    token = current_execution_ctx.set(ctx)

    try:
        # Case 1: No approval channel -> blocked, plan_mode stays True
        res_no_approval = await registry.call_tool("exit_plan_mode", {"summary": "My Plan"})
        assert "Blocked" in res_no_approval
        assert ctx.plan_mode is True

        # Case 2: Rejected by user -> plan_mode stays True
        async def _reject(action: str, summary: str, risk: str) -> str:
            return "Plan rejected: insufficient tests."

        registry.set_approval_callback(_reject)
        res_rejected = await registry.call_tool("exit_plan_mode", {"summary": "My Plan"})
        assert "Plan approval rejected" in res_rejected
        assert ctx.plan_mode is True

        # Case 3: Approved by user -> plan_mode is cleared
        async def _approve(action: str, summary: str, risk: str) -> str:
            return f"{APPROVAL_GRANTED_PREFIX}: looks good to go"

        registry.set_approval_callback(_approve)
        res_approved = await registry.call_tool("exit_plan_mode", {"summary": "My Plan"})
        assert "Plan approved" in res_approved
        assert ctx.plan_mode is False
    finally:
        current_execution_ctx.reset(token)


@pytest.mark.asyncio
async def test_c4_github_replay_lease_mismatch_refuses_force_push() -> None:
    """C4: GitHub replay refuses to force-push if remote branch moved concurrently from lease."""
    engine = BranchPublisher(
        driver=FakeSandboxDriver(),
        api_url="https://api.github.com",
        mint_app_token=AsyncMock(return_value="token"),
    )
    host = AsyncMock()
    plan = _Plan(base="base123", commits=("c1", "c2"), created=False, lease="expected_lease_sha")

    async def mock_rest(method: str, path: str, token: str, body: Any = None) -> Any:
        if method == "POST" and "refs" in path:
            return {"ref": path}
        if method == "GET" and "refs/heads/feature" in path:
            return {"object": {"sha": "concurrent_remote_sha"}}
        return {}

    engine._rest = mock_rest  # type: ignore[assignment]
    engine._create_commit = AsyncMock(return_value="new_head_sha")  # type: ignore[assignment]

    with pytest.raises(PublishError) as exc_info:
        await engine._replay(
            host=host,
            repository="org/repo",
            branch="feature",
            plan=plan,
            token="ghp_test",
            notices=[],
        )

    assert "Refusing force-push" in str(exc_info.value)
    assert "concurrently" in str(exc_info.value)
