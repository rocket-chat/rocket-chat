"""Exactly one replica runs a singleton workload, and another takes over.

Real PostgreSQL advisory locks; each ``LeaderElection`` is a separate "replica" holding its
own connection.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable

import asyncpg
import pytest
from api.events_shared import asyncpg_dsn
from api.services.cluster import LeaderElection

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL not configured")


class _Cluster:
    """Counts how many replicas run the singleton at once; the answer must never exceed one."""

    def __init__(self) -> None:
        self.concurrent = 0
        self.peak = 0


class _Replica:
    """A workload that records when it is running and can be stopped like a Socket Mode handler."""

    def __init__(self, name: str, cluster: _Cluster) -> None:
        self.name = name
        self.running = False
        self.starts = 0
        self._cluster = cluster
        self._release = asyncio.Event()

    async def workload(self) -> None:
        self.running = True
        self.starts += 1
        self._cluster.concurrent += 1
        self._cluster.peak = max(self._cluster.peak, self._cluster.concurrent)
        self._release.clear()
        try:
            await self._release.wait()
        finally:
            self.running = False
            self._cluster.concurrent -= 1

    async def stop(self) -> None:
        self._release.set()


async def _eventually(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not condition():  # noqa: ASYNC110 - polling an arbitrary predicate
            await asyncio.sleep(0.02)


def _election(role: str) -> LeaderElection:
    return LeaderElection(asyncpg_dsn(TEST_DB_URL), role, poll_seconds=0.05, heartbeat_seconds=0.1)


async def test_only_one_replica_leads_and_a_standby_takes_over() -> None:
    role = f"test-role-{uuid.uuid4().hex}"
    cluster = _Cluster()
    first, second = _Replica("a", cluster), _Replica("b", cluster)
    task_a = asyncio.create_task(_election(role).run(first.workload, first.stop))
    await _eventually(lambda: first.running)
    task_b = asyncio.create_task(_election(role).run(second.workload, second.stop))

    await asyncio.sleep(0.4)
    assert first.running and not second.running

    task_a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task_a
    await _eventually(lambda: second.running)
    assert not first.running
    assert cluster.peak == 1

    task_b.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task_b


async def test_a_dropped_connection_stops_the_workload_before_the_next_leader_starts() -> None:
    role = f"test-role-{uuid.uuid4().hex}"
    cluster = _Cluster()
    leader, standby = _Replica("a", cluster), _Replica("b", cluster)
    task_a = asyncio.create_task(_election(role).run(leader.workload, leader.stop))
    await _eventually(lambda: leader.running)

    admin = await asyncpg.connect(asyncpg_dsn(os.getenv("ADMIN_DATABASE_URL", TEST_DB_URL)))
    try:
        # Kill the leader's lock-holding session from outside, as a network partition would.
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_locks l JOIN pg_stat_activity a USING (pid) "
            "WHERE l.locktype = 'advisory' AND l.granted AND a.application_name = ''"
            "  AND a.pid <> pg_backend_pid() AND a.datname = current_database()"
        )
    finally:
        await admin.close()

    task_b = asyncio.create_task(_election(role).run(standby.workload, standby.stop))
    await _eventually(lambda: leader.starts + standby.starts >= 2)
    assert cluster.peak == 1

    for task in (task_a, task_b):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
