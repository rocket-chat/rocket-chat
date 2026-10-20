"""Unit tests for Phase 1 high-impact fixes (H1 - H7)."""

from __future__ import annotations

import shlex
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_core.tools.lsp import _build_diagnostic_command, _resolve_binary, run_diagnostics_on_file
from agent_core.tools.processes import is_valid_id
from api.auth.context import RequestContext
from api.routers.fork import ForkSessionRequest, RewindSessionRequest, fork_session, rewind_session
from api.services.artifacts import MAX_ARTIFACT_CONTENT_CHARS, InMemoryArtifactStore
from api.services.goals import InMemoryGoalStore
from api.services.waits import InMemoryWaitStore, SessionWait, claim_due_waits
from fastapi import HTTPException
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.session import (
    GoalStatus,
    SessionArtifact,
    SessionGoal,
    SessionRecord,
)


@pytest.mark.asyncio
async def test_h1_durable_waits_and_claim_due() -> None:
    """H1: Wait store registers waits and claim_due atomically claims only expired waits."""
    store = InMemoryWaitStore()
    now = int(time.time())

    w_due = SessionWait(
        id="wait_due",
        session_id="s1",
        tenant_org_id="org1",
        kind="time",
        target="30",
        deadline=now - 10,
        wake_prompt="Wake up",
    )
    w_future = SessionWait(
        id="wait_future",
        session_id="s1",
        tenant_org_id="org1",
        kind="time",
        target="60",
        deadline=now + 100,
        wake_prompt="Not yet",
    )

    await store.create_wait(w_due)
    await store.create_wait(w_future)

    claimed = await claim_due_waits(None, store, now=now)
    assert len(claimed) == 1
    assert claimed[0].id == "wait_due"
    assert claimed[0].status == "claimed"

    # Second claim returns nothing (already claimed)
    claimed2 = await claim_due_waits(None, store, now=now)
    assert len(claimed2) == 0

    # Resolving wait sets status completed
    res = await store.resolve_wait("s1", "wait_due", "org1", "completed")
    assert res is not None
    assert res.status == "completed"


def test_h2_process_id_validation() -> None:
    """H2: Validate process identifiers adhere to p<6-hex-digits>."""
    assert is_valid_id("p123456")
    assert is_valid_id("pabcdef")
    assert not is_valid_id("123456")
    assert not is_valid_id("p12345")  # too short
    assert not is_valid_id("p1234567")  # too long
    assert not is_valid_id("p12345g")  # invalid hex
    assert not is_valid_id("; rm -rf /")  # injection attempt


@pytest.mark.asyncio
async def test_h3_rewind_locked_session_refused() -> None:
    """H3: Rewind is refused with 409 if a turn is currently locked/live."""
    request = MagicMock()
    session = SessionRecord(
        session_id="s_locked",
        tenant_org_id="org1",
        tenant_user_id="u1",
        title="Session",
        source="web",
        sandbox_status=SandboxStatus.RUNNING,
        conversation_history=[],
    )

    store = AsyncMock()
    store.get_session = AsyncMock(return_value=session)
    request.app.state.session_store = store

    # Simulate locked turn
    lock = MagicMock()
    lock.locked.return_value = True
    locks = MagicMock()
    locks.lock.return_value = lock
    request.app.state.session_write_locks = locks

    request.state.context = RequestContext(
        user_id="u1", org_id="org1", email="u1@example.com", roles=["admin"]
    )

    with pytest.raises(HTTPException) as exc:
        await rewind_session("s_locked", RewindSessionRequest(target_turn=1), request)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_h4_goal_ceilings_and_wall_time_enforcement() -> None:
    """H4: Goal ceilings enforce max_wall_time_seconds and flag non-ACTIVE goals as exceeded."""
    store = InMemoryGoalStore()
    now = int(time.time())

    # Wall time limit exceeded
    goal = SessionGoal(
        id="g1",
        session_id="s_goal",
        tenant_org_id="org1",
        description="Test Goal",
        status=GoalStatus.ACTIVE,
        max_continuations=10,
        max_wall_time_seconds=60,
        created_at=now - 120,  # 2 minutes ago
    )
    await store.create_goal(goal)

    updated_goal, exceeded, warning = await store.record_consumption(
        "s_goal", "org1", tokens=100, cost_usd=0.01
    )
    assert exceeded is True
    assert "Wall time limit exceeded" in str(warning)
    assert updated_goal.status == GoalStatus.FAILED

    # Subsequent record_consumption on FAILED goal must return exceeded=True
    _, exceeded2, warning2 = await store.record_consumption(
        "s_goal", "org1", tokens=10, cost_usd=0.001
    )
    assert exceeded2 is True
    assert "Goal is not active" in str(warning2)


@pytest.mark.asyncio
async def test_h5_artifact_atomic_update_and_content_cap() -> None:
    """H5: Artifact store enforces content caps, deletes artifacts, and increments version."""
    store = InMemoryArtifactStore()

    # Content cap
    huge_content = "x" * (MAX_ARTIFACT_CONTENT_CHARS + 5000)
    art = SessionArtifact(
        id="art_cap",
        session_id="s_art",
        tenant_org_id="org1",
        title="Huge Artifact",
        filename="huge.txt",
        artifact_type="text",
        content=huge_content,
    )
    created = await store.create_artifact(art)
    assert len(created.content) <= MAX_ARTIFACT_CONTENT_CHARS

    # Atomic update increments version
    updated = await store.update_artifact("s_art", "art_cap", "org1", "Updated content")
    assert updated is not None
    assert updated.version == 2
    assert updated.content == "Updated content"

    # Delete artifacts
    await store.delete_artifacts("s_art", "org1")
    assert await store.get_artifact("s_art", "art_cap", "org1") is None


@pytest.mark.asyncio
async def test_h6_fork_metadata_sanitization() -> None:
    """H6: Fork cleanses sensitive and session-bound metadata keys."""
    request = MagicMock()
    parent = SessionRecord(
        session_id="s_parent",
        tenant_org_id="org1",
        tenant_user_id="u1",
        title="Parent Session",
        source="web",
        sandbox_status=SandboxStatus.RUNNING,
        conversation_history=[],
        metadata={
            "public_link": "https://example.com/share/secret",
            "slack_channel_id": "C12345",
            "slack_thread_ts": "123.456",
            "sandbox_id": "sbx_old",
            "plan_md": "# Architecture Plan",
        },
    )

    created_sessions: list[SessionRecord] = []
    store = AsyncMock()
    store.get_session = AsyncMock(return_value=parent)
    store.create_session = AsyncMock(side_effect=lambda r: created_sessions.append(r))
    request.app.state.session_store = store
    request.app.state.sandbox_driver = None
    request.app.state.db_manager = None
    request.app.state.transcript_store = None
    request.app.state.event_bus = None

    request.state.context = RequestContext(
        user_id="u1", org_id="org1", email="u1@example.com", roles=["admin"]
    )

    await fork_session("s_parent", ForkSessionRequest(up_to_turn=1), request)

    assert len(created_sessions) == 1
    child = created_sessions[0]
    # Sensitive keys excluded
    assert "public_link" not in child.metadata
    assert "slack_channel_id" not in child.metadata
    assert "sandbox_id" not in child.metadata
    # Safe domain metadata retained
    assert child.metadata.get("plan_md") == "# Architecture Plan"
    assert child.metadata["forked_from"] == {"session_id": "s_parent", "turn": 1}


@pytest.mark.asyncio
async def test_h7_diagnostics_probe_hardening() -> None:
    """H7: Diagnostics probe quotes file paths and resolves local binaries safely."""
    # Command builder quotes paths
    cmd_info = _build_diagnostic_command(".py", "bad file'name.py", "/workspace/dir with spaces")
    assert cmd_info is not None
    binary, command = cmd_info
    assert binary == "ruff"
    assert shlex.quote("bad file'name.py") in command

    # Resolver checks without error
    driver = FakeSandboxDriver()
    res = await _resolve_binary(driver, "sess_h7", "ruff", "/workspace")
    # Fake driver returns empty/fail for command check, so returns None safely without exception
    assert res is None or res == "ruff"

    # Execution returns None cleanly when no binary is found
    diag = await run_diagnostics_on_file(driver, "sess_h7", "test.py")
    assert diag is None
