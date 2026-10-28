"""Replicas share one event stream, one sequence and each other's decisions.

These run against real PostgreSQL ``LISTEN``/``NOTIFY``. Each ``PostgresEventBus`` is a
separate "replica" with its own listener connection and its own in-process state; one test
also publishes from a genuinely separate OS process.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.db.session import DatabaseSessionManager
from api.events_shared import PostgresEventBus, asyncpg_dsn
from api.services.cluster import ClusterRelay
from api.turns.running import _active_running_turns
from sqlalchemy import text

from specifications.interfaces.agent import ApprovalRequest, InteractiveQuestion, RunningTurnLive
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.llm import LLMGatewayProtocol

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")
REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL not configured")


@pytest.fixture
async def manager() -> AsyncIterator[DatabaseSessionManager]:
    db = DatabaseSessionManager(TEST_DB_URL)
    yield db
    await db.close()


async def _replica(manager: DatabaseSessionManager) -> PostgresEventBus:
    bus = PostgresEventBus(manager, asyncpg_dsn(TEST_DB_URL))
    await bus.start()
    return bus


@pytest.fixture
async def pair(
    manager: DatabaseSessionManager,
) -> AsyncIterator[tuple[PostgresEventBus, PostgresEventBus]]:
    first, second = await _replica(manager), await _replica(manager)
    yield first, second
    await first.close()
    await second.close()


def _sid() -> str:
    return f"sess_{uuid.uuid4().hex[:10]}"


def _token(session_id: str, text_: str) -> SessionEvent:
    return SessionEvent(session_id=session_id, event_type=EventType.TOKEN, payload={"text": text_})


async def _next(queue: asyncio.Queue[SessionEvent], seconds: float = 5.0) -> SessionEvent:
    return await asyncio.wait_for(queue.get(), seconds)


@pytest.mark.asyncio
async def test_a_subscriber_on_one_replica_sees_events_published_on_another(
    pair: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    pod_a, pod_b = pair  # the WebSocket is on A, the turn runs on B
    sid = _sid()
    queue, close = pod_a.open_queue(sid)
    try:
        await pod_b.publish(_token(sid, "hello"))
        received = await _next(queue)
    finally:
        close()

    assert received.payload == {"text": "hello"} and received.event_type == EventType.TOKEN
    assert received.session_id == sid


@pytest.mark.asyncio
async def test_all_replicas_number_a_sessions_events_from_one_sequence(
    pair: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    pod_a, pod_b = pair
    sid = _sid()
    queue, close = pod_a.open_queue(sid)
    try:
        for index in range(3):
            await pod_a.publish(_token(sid, f"a{index}"))
        for index in range(2):
            await pod_b.publish(_token(sid, f"b{index}"))
        seen = [await _next(queue) for _ in range(5)]
    finally:
        close()

    assert sorted(event.seq for event in seen) == [1, 2, 3, 4, 5]
    assert pod_a.latest_seq(sid) == 5


@pytest.mark.asyncio
async def test_a_replica_adopts_the_shared_sequence_before_serving_a_session(
    pair: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    pod_a, pod_b = pair
    sid = _sid()
    for index in range(4):
        await pod_b.publish(_token(sid, str(index)))

    assert pod_a.latest_seq(sid) == 0, "A never saw this session"
    await pod_a.attach(sid)

    assert pod_a.latest_seq(sid) == 4
    # A client holding seq 4 from B's stream is up to date; seq 9 (ahead of the cluster) is not.
    assert pod_a.replay_since(sid, 4) == ([], True)
    assert pod_a.replay_since(sid, 9)[1] is False


@pytest.mark.asyncio
async def test_a_resumed_client_replays_events_that_arrived_from_another_replica(
    pair: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    pod_a, pod_b = pair
    sid = _sid()
    queue, close = pod_a.open_queue(sid)
    try:
        for index in range(3):
            await pod_b.publish(_token(sid, str(index)))
        for _ in range(3):
            await _next(queue)
    finally:
        close()

    events, continuous = pod_a.replay_since(sid, 1)

    assert continuous and [e.payload["text"] for e in events] == ["1", "2"]


@pytest.mark.asyncio
async def test_events_larger_than_a_notify_payload_arrive_intact(
    pair: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    pod_a, pod_b = pair
    sid = _sid()
    big = "x" * 120_000 + "é✓" * 50  # many NOTIFY frames, multi-byte characters
    queue, close = pod_a.open_queue(sid)
    try:
        await pod_b.publish(
            SessionEvent(session_id=sid, event_type=EventType.FILE_DIFF, payload={"diff": big})
        )
        received = await _next(queue)
    finally:
        close()

    assert received.payload["diff"] == big


@pytest.mark.asyncio
async def test_a_publishing_replica_does_not_deliver_its_own_event_twice(
    pair: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    pod_a, _ = pair
    sid = _sid()
    queue, close = pod_a.open_queue(sid)
    try:
        await pod_a.publish(_token(sid, "once"))
        await _next(queue)
        await asyncio.sleep(0.3)
    finally:
        close()

    assert queue.empty()


@pytest.mark.asyncio
async def test_events_published_by_a_separate_process_reach_this_replica(
    pair: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    pod_a, _ = pair
    sid = _sid()
    script = (
        "import asyncio, os\n"
        "from api.db.session import DatabaseSessionManager\n"
        "from api.events_shared import PostgresEventBus, asyncpg_dsn\n"
        "from specifications.interfaces.events import EventType, SessionEvent\n"
        "async def main():\n"
        "    url = os.environ['TEST_DATABASE_URL']\n"
        "    manager = DatabaseSessionManager(url)\n"
        "    bus = PostgresEventBus(manager, asyncpg_dsn(url))\n"
        "    await bus.start()\n"
        f"    await bus.publish(SessionEvent('{sid}', EventType.STATUS_CHANGED, {{'from': 'process-b'}}))\n"
        "    await bus.close(); await manager.close()\n"
        "asyncio.run(main())\n"
    )
    queue, close = pod_a.open_queue(sid)
    try:
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            check=True,
            capture_output=True,
        )
        received = await _next(queue)
    finally:
        close()

    assert received.payload == {"from": "process-b"}


@pytest.mark.asyncio
async def test_the_listener_reconnects_after_its_connection_is_killed(
    pair: tuple[PostgresEventBus, PostgresEventBus], manager: DatabaseSessionManager
) -> None:
    pod_a, pod_b = pair
    sid = _sid()
    listener = pod_a._listener
    assert listener is not None
    backend_pid = listener.get_server_pid()
    async with manager.session() as session:
        await session.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend_pid})

    queue, close = pod_a.open_queue(sid)
    try:
        async with asyncio.timeout(15):
            while queue.empty():
                await pod_b.publish(_token(sid, "after-reconnect"))
                await asyncio.sleep(0.5)
        received = await _next(queue)
    finally:
        close()

    assert received.payload == {"text": "after-reconnect"}


# -- decisions ----------------------------------------------------------------------------


class _NoModel(LLMGatewayProtocol):
    async def chat_stream(self, request: Any) -> AsyncIterator[Any]:
        raise AssertionError("no model call expected")
        yield

    async def check_budget(self, *_: Any) -> bool:
        return True

    async def record_usage(self, *_: Any, **__: Any) -> None:
        return None


def _orchestrator(bus: PostgresEventBus) -> AsyncReActOrchestrator:
    orchestrator = AsyncReActOrchestrator(gateway=_NoModel(), registry=ToolRegistry())
    orchestrator.cluster_relay = ClusterRelay(bus, orchestrator)
    return orchestrator


@pytest.mark.asyncio
async def test_an_approval_clicked_on_another_replica_unblocks_the_waiting_turn(
    pair: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    bus_a, bus_b = pair
    turn_pod, click_pod = _orchestrator(bus_a), _orchestrator(bus_b)
    sid, action = _sid(), "ap_cluster1"
    wait = asyncio.Event()
    turn_pod.gates.register_approval(
        action, sid, wait, ApprovalRequest(action, "deploy", "ship", "high")
    )

    await click_pod.submit_human_approval(sid, action, True)

    await asyncio.wait_for(wait.wait(), 3)
    assert turn_pod.gates.get_approval_decision(action) is True


@pytest.mark.asyncio
async def test_an_answer_given_on_another_replica_reaches_the_asking_turn(
    pair: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    bus_a, bus_b = pair
    turn_pod, answer_pod = _orchestrator(bus_a), _orchestrator(bus_b)
    sid, qid = _sid(), "q_cluster1"
    wait = asyncio.Event()
    turn_pod.gates.register_question(
        qid, sid, wait, InteractiveQuestion(qid, "Which?", ["fast", "safe"])
    )

    await answer_pod.submit_question_answer(sid, qid, ["safe"], "because")

    await asyncio.wait_for(wait.wait(), 3)
    assert turn_pod.gates.get_question_answer(qid) == (["safe"], "because")


@pytest.mark.asyncio
async def test_a_tab_on_another_replica_gets_what_the_running_turn_streamed(
    pair: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    bus_a, bus_b = pair
    _orchestrator(bus_a)
    tab_pod = _orchestrator(bus_b)
    sid = _sid()
    live: RunningTurnLive = {
        "current_reasoning": "Compare the two lock orders.",
        "current_text": "The deadlock comes from",
        "seq": 42,
        "step_ids": ["call_1"],
    }

    async def live_snapshot() -> RunningTurnLive:
        return live

    # Both replicas share this process's registry; a replica never hears its own request, so
    # only the other one answers, as the replica running the turn would.
    _active_running_turns[sid] = {"handlers": SimpleNamespace(live_snapshot=live_snapshot)}
    try:
        relay = tab_pod.cluster_relay
        assert relay is not None
        assert await relay.fetch_running_turn(sid) == live
    finally:
        _active_running_turns.pop(sid, None)


@pytest.mark.asyncio
async def test_a_turn_no_replica_runs_yields_nothing_rather_than_a_wait(
    pair: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    _, bus_b = pair
    relay = _orchestrator(bus_b).cluster_relay
    assert relay is not None
    assert await relay.fetch_running_turn(_sid()) is None


@pytest.mark.asyncio
async def test_a_decision_nobody_holds_is_still_reported_as_not_found(
    pair: tuple[PostgresEventBus, PostgresEventBus], monkeypatch: pytest.MonkeyPatch
) -> None:
    from api.services import cluster

    monkeypatch.setattr(cluster, "ACK_TIMEOUT_SECONDS", 0.5)
    _, bus_b = pair
    click_pod = _orchestrator(bus_b)

    with pytest.raises(KeyError):
        await click_pod.submit_human_approval(_sid(), "ap_missing", True)


@pytest.mark.asyncio
async def test_a_gate_belonging_to_another_session_cannot_be_resolved_by_id_alone(
    pair: tuple[PostgresEventBus, PostgresEventBus], monkeypatch: pytest.MonkeyPatch
) -> None:
    from api.services import cluster

    monkeypatch.setattr(cluster, "ACK_TIMEOUT_SECONDS", 0.5)
    bus_a, bus_b = pair
    turn_pod, click_pod = _orchestrator(bus_a), _orchestrator(bus_b)
    wait = asyncio.Event()
    turn_pod.gates.register_approval(
        "ap_x", "owner_session", wait, ApprovalRequest("ap_x", "deploy", "ship", "high")
    )

    with pytest.raises(KeyError):
        await click_pod.submit_human_approval("someone_elses_session", "ap_x", True)

    assert not wait.is_set()


@pytest.mark.asyncio
async def test_cancelling_on_one_replica_halts_the_turn_running_on_another(
    pair: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    bus_a, bus_b = pair
    turn_pod, cancel_pod = _orchestrator(bus_a), _orchestrator(bus_b)
    sid = _sid()
    running = asyncio.create_task(asyncio.sleep(60))
    turn_pod._active_tasks[sid] = running
    wait = asyncio.Event()
    turn_pod.gates.register_approval(
        "ap_c", sid, wait, ApprovalRequest("ap_c", "deploy", "ship", "high")
    )

    await cancel_pod.cancel_turn(sid)

    async with asyncio.timeout(3):
        while not running.cancelled():  # noqa: ASYNC110 - waiting for the cluster message
            await asyncio.sleep(0.05)
    assert turn_pod._cancel_flags[sid] is True and wait.is_set()


@pytest.mark.asyncio
async def test_a_replica_does_not_buffer_sessions_it_does_not_serve(
    pair: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    pod_a, pod_b = pair
    sid = _sid()

    await pod_b.publish(_token(sid, "nobody here is listening"))
    await asyncio.sleep(0.3)

    assert sid not in pod_a._history and pod_a.latest_seq(sid) == 0


# -- one turn per session, cluster-wide -----------------------------------------------------


@pytest.mark.asyncio
async def test_two_replicas_never_run_a_sessions_turns_at_the_same_time() -> None:
    from api.services.cluster import ClusterSessionLocks

    replica_a = ClusterSessionLocks(asyncpg_dsn(TEST_DB_URL), poll_seconds=0.05)
    replica_b = ClusterSessionLocks(asyncpg_dsn(TEST_DB_URL), poll_seconds=0.05)
    sid = _sid()
    running: list[str] = []
    overlap: list[str] = []

    async def turn(locks: ClusterSessionLocks, name: str) -> None:
        async with locks.lock(sid):
            running.append(name)
            if len(running) > 1:
                overlap.append(name)
            await asyncio.sleep(0.3)
            running.remove(name)

    await asyncio.gather(turn(replica_a, "a"), turn(replica_b, "b"), turn(replica_a, "a2"))

    assert overlap == []


@pytest.mark.asyncio
async def test_different_sessions_do_not_block_each_other() -> None:
    from api.services.cluster import ClusterSessionLocks

    locks_a = ClusterSessionLocks(asyncpg_dsn(TEST_DB_URL), poll_seconds=0.05)
    locks_b = ClusterSessionLocks(asyncpg_dsn(TEST_DB_URL), poll_seconds=0.05)
    first, second = _sid(), _sid()

    async with locks_a.lock(first):
        async with asyncio.timeout(2):
            async with locks_b.lock(second):
                pass


@pytest.mark.asyncio
async def test_a_replica_that_dies_releases_its_sessions_lock(
    manager: DatabaseSessionManager,
) -> None:
    from api.services.cluster import ClusterSessionLocks

    holder = ClusterSessionLocks(asyncpg_dsn(TEST_DB_URL), poll_seconds=0.05)
    waiter = ClusterSessionLocks(asyncpg_dsn(TEST_DB_URL), poll_seconds=0.05)
    sid = _sid()
    acquired = asyncio.Event()

    async def hold_forever() -> None:
        async with holder.lock(sid):
            acquired.set()
            await asyncio.sleep(60)

    held = asyncio.create_task(hold_forever())
    await asyncio.wait_for(acquired.wait(), 5)

    # The holder's database connection is the only thing keeping the lock; kill it.
    async with manager.session() as session:
        await session.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE pid <> pg_backend_pid() AND query ILIKE '%pg_try_advisory_lock%'"
            )
        )

    async with asyncio.timeout(10):
        async with waiter.lock(sid):
            pass
    held.cancel()
    with pytest.raises(asyncio.CancelledError):
        await held


# -- state that must not live in one process --------------------------------------------------


@pytest.mark.asyncio
async def test_a_pairing_code_issued_on_one_replica_is_claimed_on_another(
    manager: DatabaseSessionManager,
) -> None:
    from api.services.slack_link import PairingCodes

    issuing_replica = PairingCodes(manager)
    claiming_replica = PairingCodes(manager)  # separate instance: no shared memory
    code = await issuing_replica.begin("U_SHARED", "T_SHARED")

    assert await claiming_replica.complete(code.lower()) == ("U_SHARED", "T_SHARED")
    assert await issuing_replica.complete(code) is None, "one claim only"


@pytest.mark.asyncio
async def test_two_replicas_racing_for_one_code_cannot_both_win(
    manager: DatabaseSessionManager,
) -> None:
    from api.services.slack_link import PairingCodes

    code = await PairingCodes(manager).begin("U_RACE", "T_RACE")
    results = await asyncio.gather(*(PairingCodes(manager).complete(code) for _ in range(8)))

    assert [r for r in results if r is not None] == [("U_RACE", "T_RACE")]


@pytest.mark.asyncio
async def test_only_a_hash_of_the_pairing_code_is_stored(
    manager: DatabaseSessionManager,
) -> None:
    from api.services.slack_link import PairingCodes

    code = await PairingCodes(manager).begin("U_HASH", "T_HASH")

    async with manager.session() as session:
        stored = (
            await session.execute(
                text("SELECT code_hash FROM slack_pairing_codes WHERE slack_user_id = 'U_HASH'")
            )
        ).scalar_one()
    assert stored != code and len(stored) == 64
