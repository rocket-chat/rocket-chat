"""Cross-replica delivery of decisions to, and reads of, the replica running a turn.

An approval click, an answer to a question or a cancel can land on any API replica, while the
turn that is waiting for it runs on exactly one. The receiving replica asks its peers over the
shared event bus; the replica holding the gate resolves it and acknowledges, so the user's
request still fails with "not found" when no replica has the gate (a stale or invented id).
A tab connecting to another replica mid-turn asks the same way for what the turn has streamed
since its last stored step.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from agent_core.orchestrator import AsyncReActOrchestrator

from api.events_shared import PostgresEventBus
from api.services.metrics import DEFAULT_SESSION_LOCK_CAPACITY, SessionWriteLocks
from api.turns.running import live_part
from specifications.interfaces.agent import RunningTurnLive

logger = logging.getLogger(__name__)

ACK_TIMEOUT_SECONDS = 2.0
# A tab opening a conversation waits this long at most for the replica running its turn.
RUNNING_TURN_TIMEOUT_SECONDS = 1.5


class ClusterRelay:
    """``ClusterRelayProtocol`` over the shared event bus."""

    def __init__(self, bus: PostgresEventBus, orchestrator: AsyncReActOrchestrator) -> None:
        self._bus = bus
        self._orchestrator = orchestrator
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._running_replies: dict[str, asyncio.Future[RunningTurnLive]] = {}
        bus.on_control("gate", self._on_gate_request)
        bus.on_control("gate_ack", self._on_gate_ack)
        bus.on_control("cancel", self._on_cancel)
        bus.on_control("running_turn", self._on_running_turn_request)
        bus.on_control("running_turn_reply", self._on_running_turn_reply)

    # -- outbound ------------------------------------------------------------------------

    async def forward_approval(self, session_id: str, action_id: str, approved: bool) -> bool:
        return await self._ask_peers(
            {"gate": "approval", "session_id": session_id, "id": action_id, "approved": approved}
        )

    async def forward_question(
        self,
        session_id: str,
        question_id: str,
        selected_options: list[str],
        custom_text: str | None,
    ) -> bool:
        return await self._ask_peers(
            {
                "gate": "question",
                "session_id": session_id,
                "id": question_id,
                "selected_options": selected_options,
                "custom_text": custom_text,
            }
        )

    async def broadcast_cancel(self, session_id: str) -> None:
        await self._bus.publish_control("cancel", {"session_id": session_id})

    async def fetch_running_turn(self, session_id: str) -> RunningTurnLive | None:
        request_id = uuid.uuid4().hex
        reply: asyncio.Future[RunningTurnLive] = asyncio.get_running_loop().create_future()
        self._running_replies[request_id] = reply
        try:
            await self._bus.publish_control(
                "running_turn", {"session_id": session_id, "request_id": request_id}
            )
            return await asyncio.wait_for(reply, timeout=RUNNING_TURN_TIMEOUT_SECONDS)
        except TimeoutError:
            return None
        finally:
            self._running_replies.pop(request_id, None)

    async def _ask_peers(self, request: dict[str, Any]) -> bool:
        request_id = uuid.uuid4().hex
        waiter: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = waiter
        try:
            await self._bus.publish_control("gate", {**request, "request_id": request_id})
            return await asyncio.wait_for(waiter, timeout=ACK_TIMEOUT_SECONDS)
        except TimeoutError:
            return False
        finally:
            self._pending.pop(request_id, None)

    # -- inbound -------------------------------------------------------------------------

    async def _on_gate_request(self, body: dict[str, Any]) -> None:
        # Resolve against local state only: re-forwarding would bounce the request around.
        gates = self._orchestrator.gates
        try:
            if body["gate"] == "approval":
                gates.submit_human_approval(body["session_id"], body["id"], bool(body["approved"]))
            else:
                gates.submit_question_answer(
                    body["session_id"],
                    body["id"],
                    list(body.get("selected_options") or []),
                    body.get("custom_text"),
                )
        except KeyError:
            return  # not ours; the replica that holds the gate answers
        await self._bus.publish_control("gate_ack", {"request_id": body["request_id"]})

    async def _on_gate_ack(self, body: dict[str, Any]) -> None:
        waiter = self._pending.get(body["request_id"])
        if waiter is not None and not waiter.done():
            waiter.set_result(True)

    async def _on_cancel(self, body: dict[str, Any]) -> None:
        await self._orchestrator.cancel_turn(body["session_id"], propagate=False)

    async def _on_running_turn_request(self, body: dict[str, Any]) -> None:
        live = await live_part(str(body["session_id"]))
        if live is None:
            return  # not running here; the replica that runs it answers
        await self._bus.publish_control(
            "running_turn_reply", {"request_id": body["request_id"], "live": live}
        )

    async def _on_running_turn_reply(self, body: dict[str, Any]) -> None:
        reply = self._running_replies.get(body["request_id"])
        if reply is not None and not reply.done():
            reply.set_result(_running_turn_live(body["live"]))


def _running_turn_live(raw: dict[str, Any]) -> RunningTurnLive:
    return {
        "current_reasoning": str(raw.get("current_reasoning") or ""),
        "current_text": str(raw.get("current_text") or ""),
        "seq": int(raw.get("seq") or 0),
        "step_ids": [str(step_id) for step_id in raw.get("step_ids") or []],
    }


class ClusterSessionLocks(SessionWriteLocks):
    """Single-writer-per-session across every API replica.

    The in-process lock still queues local contenders, so at most one task per replica ever
    contends in the database. The cluster lock is a PostgreSQL session-level advisory lock
    held on a dedicated connection for the life of the turn: if the replica dies, the
    connection drops and the lock is released with it - there is no lease to expire.
    """

    def __init__(
        self,
        dsn: str,
        capacity: int = DEFAULT_SESSION_LOCK_CAPACITY,
        poll_seconds: float = 0.25,
    ) -> None:
        super().__init__(capacity)
        self._dsn = dsn
        self._poll_seconds = poll_seconds

    @asynccontextmanager
    async def lock(self, session_id: str) -> AsyncIterator[None]:
        async with super().lock(session_id):
            async with self._hold_cluster_lock(session_id):
                yield

    @asynccontextmanager
    async def _hold_cluster_lock(self, session_id: str) -> AsyncIterator[None]:
        connection = await asyncpg.connect(self._dsn)
        acquired = False
        try:
            while not acquired:
                acquired = bool(
                    await connection.fetchval(
                        "SELECT pg_try_advisory_lock(hashtextextended($1, 0))", session_id
                    )
                )
                if not acquired:
                    await asyncio.sleep(self._poll_seconds)
            yield
        finally:
            if acquired:
                with contextlib.suppress(Exception):
                    await connection.fetchval(
                        "SELECT pg_advisory_unlock(hashtextextended($1, 0))", session_id
                    )
            with contextlib.suppress(Exception):
                await connection.close(timeout=2)


class LeaderElection:
    """Run a singleton workload on exactly one replica.

    Slack Socket Mode delivers each event to *every* open connection, so two replicas would
    answer the same message twice. The leader holds a PostgreSQL advisory lock on a dedicated
    connection; if the replica dies the connection drops and a standby takes over within one
    poll interval - there is no lease to expire. A heartbeat query notices a connection that
    died quietly, so the workload stops before a second leader starts.
    """

    def __init__(
        self,
        dsn: str,
        name: str,
        *,
        poll_seconds: float = 5.0,
        heartbeat_seconds: float = 5.0,
    ) -> None:
        self._dsn = dsn
        self._name = name
        self._poll_seconds = poll_seconds
        self._heartbeat_seconds = heartbeat_seconds

    async def run(
        self,
        workload: Callable[[], Coroutine[Any, Any, None]],
        stop: Callable[[], Awaitable[None]],
    ) -> None:
        """Hold leadership and run ``workload``; ``stop`` must make it return."""
        while True:
            try:
                connection = await asyncpg.connect(self._dsn, timeout=10)
            except (OSError, asyncpg.PostgresError, TimeoutError) as err:
                logger.warning(
                    "Leader election (%s) cannot reach the database: %s", self._name, err
                )
                await asyncio.sleep(self._poll_seconds)
                continue
            try:
                if await connection.fetchval(
                    "SELECT pg_try_advisory_lock(hashtextextended($1, 0))", self._name
                ):
                    logger.info("This replica is now the %s leader.", self._name)
                    await self._lead(connection, workload, stop)
                    logger.warning("Lost %s leadership; standing by.", self._name)
            except (OSError, asyncpg.PostgresError) as err:
                logger.warning("Leader election (%s) interrupted: %s", self._name, err)
            finally:
                with contextlib.suppress(Exception):
                    await connection.close(timeout=2)
            await asyncio.sleep(self._poll_seconds)

    async def _lead(
        self,
        connection: asyncpg.Connection,
        workload: Callable[[], Coroutine[Any, Any, None]],
        stop: Callable[[], Awaitable[None]],
    ) -> None:
        connection_lost = asyncio.Event()
        # Fires as soon as the server drops the session (the lock is released at that same
        # instant), so the workload stops before a standby can win the lock. The heartbeat
        # below covers a connection that died without a close notification.
        connection.add_termination_listener(lambda _connection: connection_lost.set())
        work: asyncio.Task[None] = asyncio.create_task(workload())
        lost = asyncio.create_task(connection_lost.wait())
        try:
            while not work.done() and not connection_lost.is_set():
                await asyncio.wait(
                    {work, lost},
                    timeout=self._heartbeat_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if work.done() or connection_lost.is_set():
                    break
                try:
                    await connection.fetchval("SELECT 1", timeout=self._heartbeat_seconds)
                except (OSError, asyncpg.PostgresError, TimeoutError):
                    return
        finally:
            lost.cancel()
            await stop()
            work.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await work
