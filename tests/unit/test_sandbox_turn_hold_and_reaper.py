"""Unit tests for reason-scoped sandbox holds and cluster-safe inactivity reaper."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from api.turns.context import TurnRequest
from api.turns.runner import TurnRunner
from sandbox_driver.fake_driver import FakeSandboxDriver
from sandbox_driver.k8s_driver import K8sSandboxDriver

from specifications.interfaces.events import EventBusProtocol
from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol


def test_fake_driver_reason_scoped_hold():
    driver = FakeSandboxDriver()
    session_id = "sess-123"

    # Start a process hold for 1000s
    driver.hold_awake(session_id, 1000, reason="process")
    assert driver.is_held(session_id) is True

    # Releasing turn hold does not release process hold
    driver.hold_awake(session_id, 0, reason="turn")
    assert driver.is_held(session_id) is True

    # Now hold awake with turn as well
    driver.hold_awake(session_id, 500, reason="turn")
    assert driver.is_held(session_id) is True

    # Releasing process hold leaves turn hold active
    driver.hold_awake(session_id, 0, reason="process")
    assert driver.is_held(session_id) is True

    # Releasing turn hold leaves nothing held
    driver.hold_awake(session_id, 0, reason="turn")
    assert driver.is_held(session_id) is False


@pytest.mark.asyncio
async def test_k8s_driver_reason_scoped_hold_and_annotations():
    driver = K8sSandboxDriver()
    session_id = "sess-k8s"

    mock_client = AsyncMock()
    driver._core_v1 = mock_client
    driver._ws_core_v1 = mock_client

    mock_pod = MagicMock()
    mock_pod.metadata.annotations = {}
    mock_client.read_namespaced_pod = AsyncMock(return_value=mock_pod)
    mock_client.patch_namespaced_pod = AsyncMock()

    # Hold with reason "turn"
    driver.hold_awake(session_id, 600, reason="turn")
    assert driver.is_held(session_id) is True

    # Yield control to let the created background task _sync_protection run
    await asyncio.sleep(0.01)
    mock_client.patch_namespaced_pod.assert_awaited()

    # Hold with reason "process"
    driver.hold_awake(session_id, 600, reason="process")
    assert driver.is_held(session_id) is True

    # Clear "turn" hold
    driver.hold_awake(session_id, 0, reason="turn")
    assert driver.is_held(session_id) is True

    # Clear "process" hold
    driver.hold_awake(session_id, 0, reason="process")
    assert driver.is_held(session_id) is False


@pytest.mark.asyncio
async def test_reaper_skips_active_and_db_idle_sessions():
    driver = FakeSandboxDriver()
    sess_active = "sess-running"
    sess_recent = "sess-recent"
    sess_stale = "sess-stale"

    now = time.time()
    # All 3 sandboxes are running
    driver._status[sess_active] = SandboxStatus.RUNNING
    driver._status[sess_recent] = SandboxStatus.RUNNING
    driver._status[sess_stale] = SandboxStatus.RUNNING

    driver._last_activity[sess_active] = now - 3600
    driver._last_activity[sess_recent] = now - 3600
    driver._last_activity[sess_stale] = now - 3600

    # sess_active is currently running a turn in DB
    active_sessions = {sess_active}
    # sess_recent completed a turn 30 seconds ago
    session_idleness = {sess_recent: now - 30, sess_stale: now - 3600}

    paused = await driver.pause_inactive_sandboxes(
        threshold_seconds=300,
        active_sessions=active_sessions,
        session_idleness=session_idleness,
    )

    # Only sess_stale should be paused; sess_active and sess_recent must be kept alive!
    assert paused == [sess_stale]
    assert driver._status[sess_stale] == SandboxStatus.HIBERNATED
    assert driver._status[sess_active] == SandboxStatus.RUNNING
    assert driver._status[sess_recent] == SandboxStatus.RUNNING


@pytest.mark.asyncio
async def test_turn_runner_holds_and_releases_sandbox():
    driver = FakeSandboxDriver()
    session_id = "sess-turn-hold"

    store = AsyncMock(spec=SessionStoreProtocol)
    store.get_session = AsyncMock(
        return_value=SessionRecord(
            session_id=session_id,
            tenant_org_id="org-1",
            tenant_user_id="user-1",
            title="test",
        )
    )
    store.mutate_session = AsyncMock()

    event_bus = AsyncMock(spec=EventBusProtocol)
    orchestrator = MagicMock()

    req = TurnRequest(
        session_id=session_id,
        prompt="hello",
        store=store,
        event_bus=event_bus,
        orchestrator=orchestrator,
        driver=driver,
        registry=None,
        db_mgr=None,
    )

    runner = TurnRunner(req)

    # Start turn hold
    runner._start_turn_hold()
    assert runner._hold_task is not None
    # Let event loop tick once for the hold to register
    await asyncio.sleep(0.01)

    assert driver.is_held(session_id) is True

    # Stop turn hold
    await runner._stop_turn_hold()
    assert runner._hold_task is None
    assert driver.is_held(session_id) is False
