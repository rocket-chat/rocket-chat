"""Event bus shared by every API replica over PostgreSQL ``LISTEN``/``NOTIFY``.

A session's WebSocket can be served by one replica while its turn runs on another. Each
replica keeps the in-process fan-out and replay buffer of :class:`AsyncIOEventBus` and adds
two things: a database-backed per-session sequence (so every replica numbers events
identically) and a broadcast channel carrying published events - and a few control messages
such as "resolve this approval" or "cancel that turn" - to the other replicas.

Delivery is at-most-once, like the in-process bus: a replica that is disconnected from the
database misses events, and clients recover through the existing resume/snapshot protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
from sqlalchemy import text

from api.db.session import DatabaseSessionManager
from api.events import AsyncIOEventBus
from specifications.interfaces.events import EventType, SessionEvent

logger = logging.getLogger(__name__)

CHANNEL = "rocket_events"
# NOTIFY payloads are capped at 8000 bytes; stay well under it and chunk anything larger.
MAX_CHUNK_CHARS = 6000
CHUNK_REASSEMBLY_TTL_SECONDS = 30.0
RECONNECT_DELAY_SECONDS = 2.0

ControlHandler = Callable[[dict[str, Any]], Awaitable[None]]


def encode_event(event: SessionEvent) -> str:
    return json.dumps(
        {
            "session_id": event.session_id,
            "event_type": event.event_type.value,
            "payload": event.payload,
            "timestamp_ms": event.timestamp_ms,
            "seq": event.seq,
        },
        default=str,
    )


def decode_event(document: str) -> SessionEvent:
    data = json.loads(document)
    return SessionEvent(
        session_id=data["session_id"],
        event_type=EventType(data["event_type"]),
        payload=data["payload"],
        timestamp_ms=data["timestamp_ms"],
        seq=data["seq"],
    )


def asyncpg_dsn(sqlalchemy_url: str) -> str:
    """The plain ``postgresql://`` form asyncpg expects, from a SQLAlchemy URL."""
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class PostgresEventBus(AsyncIOEventBus):
    """``AsyncIOEventBus`` whose events and control messages reach every replica."""

    def __init__(
        self,
        manager: DatabaseSessionManager,
        listen_dsn: str,
        *,
        instance_id: str | None = None,
        history_size: int = 2000,
    ) -> None:
        super().__init__(history_size=history_size)
        self._manager = manager
        self._listen_dsn = listen_dsn
        self.instance_id = instance_id or uuid.uuid4().hex
        self._control_handlers: dict[str, ControlHandler] = {}
        self._chunks: dict[str, tuple[float, dict[int, str]]] = {}
        self._listener: asyncpg.Connection | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self._listening = asyncio.Event()
        self._dropped = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()

    # -- lifecycle -----------------------------------------------------------------------

    async def start(self) -> None:
        """Begin listening; returns once the first subscription is active."""
        self._supervisor = asyncio.create_task(self._supervise())
        await asyncio.wait_for(self._listening.wait(), timeout=10)

    async def close(self) -> None:
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
            self._supervisor = None
        await self._drop_listener()

    async def _supervise(self) -> None:
        """Keep one LISTEN connection alive, reconnecting if it drops."""
        while True:
            try:
                self._dropped = asyncio.Event()
                self._listener = await asyncpg.connect(self._listen_dsn)
                self._listener.add_termination_listener(self._on_listener_closed)
                await self._listener.add_listener(CHANNEL, self._on_notification)
                self._listening.set()
                await self._dropped.wait()
                logger.warning("Event bus listener connection closed; reconnecting.")
            except asyncio.CancelledError:
                raise
            except Exception as err:
                logger.warning("Event bus listener failed (%s); retrying.", err)
            finally:
                await self._drop_listener()
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    def _on_listener_closed(self, _connection: object) -> None:
        self._dropped.set()

    async def _drop_listener(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None and not listener.is_closed():
            with contextlib.suppress(Exception):
                await listener.close(timeout=2)

    # -- publishing ----------------------------------------------------------------------

    async def attach(self, session_id: str) -> None:
        """Adopt the shared sequence for a session this replica is about to serve."""
        shared = await self._read_counter(session_id)
        async with self._lock:
            if shared > self._last_seq.get(session_id, 0):
                self._last_seq[session_id] = shared
            # From here this replica is interested in the session: it buffers peers' events for
            # replay even in the instant before the client's subscription opens.
            self._history.setdefault(session_id, self._new_history())

    async def publish(self, event: SessionEvent) -> None:
        event.seq = await self._next_seq(event.session_id)
        await self._deliver_locally(event)
        await self._broadcast({"k": "event", "d": encode_event(event)})

    async def _next_seq(self, session_id: str) -> int:
        try:
            async with self._manager.session() as session:
                value = await session.scalar(
                    text(
                        "INSERT INTO session_event_counters (session_id, last_seq, updated_at) "
                        "VALUES (:sid, 1, :now) ON CONFLICT (session_id) DO UPDATE "
                        "SET last_seq = session_event_counters.last_seq + 1, "
                        "updated_at = EXCLUDED.updated_at RETURNING last_seq"
                    ),
                    {"sid": session_id, "now": int(time.time())},
                )
            return int(value)
        except Exception as err:
            # Streaming must survive a database blip; the local counter keeps this replica's
            # clients coherent and the resume protocol heals any cross-replica divergence.
            logger.warning("Shared event sequence unavailable (%s); using local counter.", err)
            return self._last_seq.get(session_id, 0) + 1

    async def _read_counter(self, session_id: str) -> int:
        try:
            async with self._manager.session() as session:
                value = await session.scalar(
                    text("SELECT last_seq FROM session_event_counters WHERE session_id = :sid"),
                    {"sid": session_id},
                )
            return int(value or 0)
        except Exception as err:
            logger.warning("Could not read the shared event sequence: %s", err)
            return 0

    async def _deliver_locally(self, event: SessionEvent) -> None:
        async with self._lock:
            self._last_seq[event.session_id] = max(
                self._last_seq.get(event.session_id, 0), event.seq
            )
            history = self._history.setdefault(event.session_id, self._new_history())
            history.append(event)
            queues = list(self._subscribers.get(event.session_id, set()))
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def evict(self, session_id: str) -> None:
        super().evict(session_id)
        with contextlib.suppress(RuntimeError):
            task = asyncio.get_running_loop().create_task(self._delete_counter(session_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _delete_counter(self, session_id: str) -> None:
        try:
            async with self._manager.session() as session:
                await session.execute(
                    text("DELETE FROM session_event_counters WHERE session_id = :sid"),
                    {"sid": session_id},
                )
        except Exception as err:
            logger.debug("Could not drop the shared event counter: %s", err)

    # -- control messages ----------------------------------------------------------------

    def on_control(self, kind: str, handler: ControlHandler) -> None:
        """Run ``handler`` for control messages of ``kind`` sent by any replica."""
        self._control_handlers[kind] = handler

    async def publish_control(self, kind: str, body: dict[str, Any]) -> None:
        """Send a control message to every *other* replica."""
        await self._broadcast({"k": "control", "t": kind, "d": json.dumps(body, default=str)})

    # -- transport -----------------------------------------------------------------------

    async def _broadcast(self, message: dict[str, Any]) -> None:
        envelope = json.dumps({**message, "o": self.instance_id}, default=str)
        parts = [
            envelope[i : i + MAX_CHUNK_CHARS] for i in range(0, len(envelope), MAX_CHUNK_CHARS)
        ] or [""]
        message_id = uuid.uuid4().hex[:12]
        try:
            async with self._manager.session() as session:
                for index, part in enumerate(parts):
                    frame = json.dumps({"m": message_id, "i": index, "n": len(parts), "p": part})
                    await session.execute(
                        text("SELECT pg_notify(:channel, :payload)"),
                        {"channel": CHANNEL, "payload": frame},
                    )
        except Exception as err:
            logger.warning("Could not broadcast to other replicas: %s", err)

    def _on_notification(
        self, _connection: object, _pid: int, _channel: str, payload: object
    ) -> None:
        task = asyncio.get_running_loop().create_task(self._handle_frame(str(payload)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle_frame(self, payload: str) -> None:
        try:
            frame = json.loads(payload)
            envelope = self._reassemble(frame)
            if envelope is None or envelope.get("o") == self.instance_id:
                return
            if envelope["k"] == "event":
                event = decode_event(envelope["d"])
                # Every replica hears every event; only buffer sessions this one serves, or
                # each replica would hold the whole cluster's streams in memory.
                if event.session_id in self._subscribers or event.session_id in self._history:
                    await self._deliver_locally(event)
            elif envelope["k"] == "control":
                handler = self._control_handlers.get(envelope["t"])
                if handler is not None:
                    await handler(json.loads(envelope["d"]))
        except Exception:
            logger.exception("Dropped a malformed cluster message")

    def _reassemble(self, frame: dict[str, Any]) -> dict[str, Any] | None:
        now = time.monotonic()
        for stale in [
            m for m, (seen, _) in self._chunks.items() if now - seen > CHUNK_REASSEMBLY_TTL_SECONDS
        ]:
            del self._chunks[stale]
        if frame["n"] == 1:
            return dict(json.loads(frame["p"]))
        _, parts = self._chunks.setdefault(frame["m"], (now, {}))
        parts[frame["i"]] = frame["p"]
        if len(parts) < frame["n"]:
            return None
        del self._chunks[frame["m"]]
        return dict(json.loads("".join(parts[i] for i in range(frame["n"]))))
