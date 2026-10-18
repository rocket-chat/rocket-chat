"""Unit tests verifying Phase 3 and Phase 4 fixes and enhancements."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from api.bootstrap.background import BackgroundServices
from api.bootstrap.core import CoreServices
from api.routers.goals import router as goals_router
from api.services.compaction import InMemoryCompactionStore
from api.services.goals import InMemoryGoalStore
from api.services.waits import InMemoryWaitStore, SessionWait
from fastapi import FastAPI
from fastapi.testclient import TestClient

from specifications.interfaces.compaction import CompactionCheckpoint
from specifications.interfaces.session import GoalStatus, SessionGoal, SessionRecord


def _build_test_app() -> tuple[
    FastAPI, InMemoryGoalStore, InMemoryWaitStore, InMemoryCompactionStore
]:
    app = FastAPI()
    app.include_router(goals_router)

    goal_store = InMemoryGoalStore()
    wait_store = InMemoryWaitStore()
    compaction_store = InMemoryCompactionStore()

    session_store = MagicMock()
    record = SessionRecord(
        session_id="test-session-1",
        tenant_org_id="default_org",
        tenant_user_id="user-1",
        title="Test Session",
    )
    session_store.get_session = AsyncMock(return_value=record)

    event_bus = MagicMock()
    event_bus.publish = AsyncMock()

    orchestrator = MagicMock()

    app.state.session_store = session_store
    app.state.goal_store = goal_store
    app.state.wait_store = wait_store
    app.state.compaction_store = compaction_store
    app.state.event_bus = event_bus
    app.state.orchestrator = orchestrator

    return app, goal_store, wait_store, compaction_store


@pytest.mark.asyncio
async def test_p1_goals_api_get_and_cancel() -> None:
    """Verify GET /v1/sessions/{id}/goals returns caps and POST .../cancel halts goal."""
    app, goal_store, _, _ = _build_test_app()
    goal = SessionGoal(
        id="goal-123",
        session_id="test-session-1",
        tenant_org_id="default_org",
        description="Build rocket feature",
        status=GoalStatus.ACTIVE,
        max_wall_time_seconds=3600,
        max_total_tokens=100_000,
        max_cost_usd=5.0,
        max_continuations=5,
        consumed_tokens=25_000,
        consumed_cost_usd=1.25,
        continuation_count=1,
        created_at=int(time.time()),
    )
    await goal_store.create_goal(goal)

    with TestClient(app) as client:
        # GET goal
        res = client.get("/v1/sessions/test-session-1/goals", headers={"X-Actor-User-Id": "user-1"})
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == "goal-123"
        assert data["max_wall_time_seconds"] == 3600
        assert data["max_total_tokens"] == 100_000
        assert data["max_cost_usd"] == 5.0
        assert data["consumed_tokens"] == 25_000

        # CANCEL goal
        cancel_res = client.post(
            "/v1/sessions/test-session-1/goals/cancel",
            headers={"X-Actor-User-Id": "user-1"},
        )
        assert cancel_res.status_code == 200
        assert cancel_res.json()["status"] == "cancelled"

        updated = await goal_store.get_goal("test-session-1", "default_org")
        assert updated is not None
        assert updated.status == GoalStatus.CANCELLED


@pytest.mark.asyncio
async def test_p1_waits_api_list_and_cancel() -> None:
    """Verify GET /v1/sessions/{id}/waits and POST .../waits/{wait_id}/cancel."""
    app, _, wait_store, _ = _build_test_app()
    wait = SessionWait(
        id="wait-abc",
        session_id="test-session-1",
        tenant_org_id="default_org",
        kind="time",
        target="300",
        deadline=int(time.time()) + 300,
        status="waiting",
        created_at=int(time.time()),
    )
    await wait_store.create_wait(wait)

    with TestClient(app) as client:
        # GET active waits
        res = client.get("/v1/sessions/test-session-1/waits", headers={"X-Actor-User-Id": "user-1"})
        assert res.status_code == 200
        waits = res.json()
        assert len(waits) == 1
        assert waits[0]["id"] == "wait-abc"
        assert waits[0]["kind"] == "time"

        # CANCEL wait
        cancel_res = client.post(
            "/v1/sessions/test-session-1/waits/wait-abc/cancel",
            headers={"X-Actor-User-Id": "user-1"},
        )
        assert cancel_res.status_code == 200
        assert cancel_res.json()["status"] == "cancelled"

        resolved = await wait_store.get_wait("test-session-1", "wait-abc", "default_org")
        assert resolved is not None
        assert resolved.status == "cancelled"


@pytest.mark.asyncio
async def test_p1_manual_compact_and_list() -> None:
    """Verify manual /compact triggers orchestrator compaction and records checkpoint."""
    app, _, _, compaction_store = _build_test_app()

    mock_result = MagicMock()
    mock_result.tokens_before = 5000
    mock_result.tokens_after = 1200
    mock_result.compacted_count = 8
    mock_result.summary = "Compacted summary of previous conversation"
    app.state.orchestrator.compact_history = AsyncMock(return_value=mock_result)

    # Pre-seed one compaction checkpoint
    cp = CompactionCheckpoint(
        id="cmp_123",
        session_id="test-session-1",
        org_id="default_org",
        boundary_seq=10,
        summary="Earlier checkpoint",
        compacted_messages=[],
        tokens_before=3000,
        tokens_after=800,
        created_at=int(time.time()),
    )
    await compaction_store.record_compaction(cp)

    with TestClient(app) as client:
        # Trigger compact
        res = client.post(
            "/v1/sessions/test-session-1/compact", headers={"X-Actor-User-Id": "user-1"}
        )
        assert res.status_code == 200
        assert res.json()["status"] == "compacted"
        assert res.json()["tokens_before"] == 5000
        assert res.json()["tokens_after"] == 1200

        # List compactions
        list_res = client.get(
            "/v1/sessions/test-session-1/compactions", headers={"X-Actor-User-Id": "user-1"}
        )
        assert list_res.status_code == 200
        history = list_res.json()
        assert len(history) >= 1
        assert history[0]["id"] == "cmp_123"


@pytest.mark.asyncio
async def test_p2_slack_artifact_updated_renderer() -> None:
    """Verify SlackTurnStreamMixin._render_artifact_updated renders updated artifact notice."""
    from slack_assistant.turn_stream import SlackTurnStreamMixin, TurnOutcome, TurnView

    mixin = SlackTurnStreamMixin()
    say_mock = AsyncMock()
    view = TurnView(
        session_id="s1",
        channel_id="C123",
        thread_ts="1234.5678",
        requester_slack_id="U1",
        say_fn=say_mock,
        status_fn=None,
        outcome=TurnOutcome(),
    )

    payload = {
        "title": "Architecture Plan",
        "filename": "ARCH.md",
        "version": 2,
    }
    await mixin._render_artifact_updated(payload, view)
    assert say_mock.called
    call_kwargs = say_mock.call_args[1]
    assert "Artifact Updated" in call_kwargs["text"]
    assert "ARCH.md" in call_kwargs["text"]
    assert "v2" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_background_waits_sweeper() -> None:
    """Verify BackgroundServices._start_waits_sweeper sweeps due waits and wakes sessions."""
    app, _, wait_store, _ = _build_test_app()
    core = MagicMock(spec=CoreServices)
    core.db_manager = None
    core.sandbox_driver = MagicMock()
    core.session_store = app.state.session_store

    bg = BackgroundServices(app, core)

    # Create a due wait (deadline in the past)
    past = int(time.time()) - 10
    due_wait = SessionWait(
        id="wait-due-1",
        session_id="test-session-1",
        tenant_org_id="default_org",
        kind="time",
        target="10",
        deadline=past,
        status="waiting",
        created_at=past - 10,
    )
    await wait_store.create_wait(due_wait)

    with patch("api.bootstrap.background._spawn_background_turn") as mock_spawn:
        # Manually invoke _wake_claimed_wait
        claimed_waits = [due_wait]
        for w in claimed_waits:
            await bg._wake_claimed_wait(w, wait_store)

        assert mock_spawn.called
        assert mock_spawn.call_args[1]["session_id"] == "test-session-1"

        resolved = await wait_store.get_wait("test-session-1", "wait-due-1", "default_org")
        assert resolved is not None
        assert resolved.status == "completed"
