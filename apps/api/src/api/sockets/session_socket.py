"""One live connection to a session: authentication, the connect snapshot, frame handling."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, ClassVar

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from api.auth.access import _check_session_access
from api.auth.api_tokens import GUEST_TOKEN_SOURCE
from api.auth.identity import (
    attach_api_token_identity,
    resolve_identity,
    resolve_token_identity,
)
from api.auth.roles import with_live_roles
from api.db.session import current_tenant_org_id
from api.db.store import TurnTraceStore
from api.services.decision_answer import decision_answer_prompt
from api.services.metrics import (
    websocket_connection_closed,
    websocket_connection_opened,
)
from api.services.pending_gates import pending_gates
from api.services.queued_messages import QueuedMessageStore, hold_for_running_turn
from api.services.session_support import apply_sharing
from api.services.turn_trace import session_running_turn
from api.turns.context import TurnSelection
from api.turns.gates import _forget_pending_decisions, take_stored_question
from api.turns.runner import _active_running_turns
from api.turns.tasks import _spawn_background_turn
from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent
from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionStoreProtocol

logger = logging.getLogger(__name__)

# Client frames that mutate the session; the read-only role may only observe.
WRITE_FRAMES = frozenset(
    {
        "turn",
        "action",
        "cancel",
        "interrupt",
        "abort",
        "answer_question",
        "approval",
        "share",
        "cancel_message",
    }
)
_TURN_FRAMES = frozenset({"turn", "action"})
_CANCEL_FRAMES = frozenset({"cancel", "interrupt", "abort"})

FrameHandler = Callable[[dict[str, Any]], Awaitable[None]]


class SessionSocket:
    """Serves one WebSocket for one session.

    The connect snapshot must lose nothing. The live queue is opened *before* the session
    record is read, so no event published afterwards can be missed. The sequence number is
    noted before that read: events published since then (and those of a turn still in
    flight, whose answer the record does not hold yet) travel in the ``sync`` frame as a
    ``catch_up``, which the client folds in idempotently. ``_snapshot`` itself is
    synchronous so the gates, the catch-up and ``last_seq`` are one consistent read.
    """

    _FRAME_HANDLERS: ClassVar[dict[str, str]] = {
        **dict.fromkeys(_TURN_FRAMES, "_on_turn"),
        **dict.fromkeys(_CANCEL_FRAMES, "_on_cancel"),
        "resume": "_on_resume",
        "answer_question": "_on_answer_question",
        "approval": "_on_approval",
        "share": "_on_share",
        "cancel_message": "_on_cancel_message",
        "ping": "_on_ping",
    }

    def __init__(self, websocket: WebSocket, session_id: str, token: str = "") -> None:
        self._ws = websocket
        self._session_id = session_id
        self._token = token
        state = websocket.app.state
        self._store: SessionStoreProtocol = state.session_store
        self._event_bus: EventBusProtocol = state.event_bus
        self._orchestrator: AsyncReActOrchestrator = state.orchestrator
        self._driver: SandboxDriverProtocol | None = getattr(state, "sandbox_driver", None)
        self._registry: ToolRegistry | None = getattr(state, "tool_registry", None)
        self._db = getattr(state, "db_manager", None)
        self._user_id = ""
        self._org_id = ""
        self._can_write = False
        self._can_share = False
        self._is_admin = False
        self._stored_decision: dict[str, Any] | None = None
        self._running_turn: dict[str, Any] | None = None
        self._session: Any = None
        self._handlers: dict[str, FrameHandler] = {
            frame: getattr(self, method) for frame, method in self._FRAME_HANDLERS.items()
        }

    @classmethod
    def accepted_frames(cls) -> frozenset[str]:
        return frozenset(cls._FRAME_HANDLERS)

    async def serve(self) -> None:
        if not await self._authorise():
            return
        await self._ws.accept()
        websocket_connection_opened()
        if getattr(self._ws.app.state, "draining", False):
            await self._reject_draining()
            websocket_connection_closed()
            return
        await self._event_bus.attach(self._session_id)
        queue, close_queue = self._event_bus.open_queue(self._session_id)
        send_task: asyncio.Task[None] | None = None
        try:
            seq_before_read = self._event_bus.latest_seq(self._session_id)
            await self._load_baseline()
            snapshot, floor_seq = self._snapshot(seq_before_read)
            await self._ws.send_json(snapshot)
            send_task = asyncio.create_task(self._stream_events(queue, floor_seq))
            await self._receive_frames()
        except WebSocketDisconnect:
            logger.debug("WebSocket client disconnected for session %s", self._session_id)
        finally:
            close_queue()
            websocket_connection_closed()
            if send_task is not None:
                send_task.cancel()
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass

    async def _authorise(self) -> bool:
        # Identity comes only from the shared resolver (cookie, bearer header, or the `token`
        # query parameter browsers use because WebSockets cannot set headers). The session
        # ACL is always enforced, however the deployment authenticates.
        await attach_api_token_identity(self._ws, self._db, self._token)
        caller = resolve_identity(self._ws) or resolve_token_identity(self._token)
        if caller is not None:
            caller = await with_live_roles(caller, self._db)
        if caller is None:
            await self._ws.close(code=1008, reason="Unauthorized: authentication required")
            return False
        # The tenant middleware does not run for WebSockets; without this, row-level
        # security hides every session from the socket.
        current_tenant_org_id.set(caller.org_id)
        self._user_id = caller.user_id
        self._org_id = caller.org_id
        roles = list(caller.roles)
        self._can_write = "readonly" not in roles
        self._is_admin = "admin" in roles

        session = await self._store.get_session(self._session_id)
        if not session or session.tenant_org_id != caller.org_id:
            await self._ws.close(code=1008, reason="Session not found")
            return False
        try:
            _check_session_access(session, self._user_id, roles, require_write=False)
        except HTTPException:
            await self._ws.close(code=1008, reason="Forbidden: Access to session denied")
            return False
        self._session = session
        # A guest token holds its conversations but does not share them.
        self._can_share = self._is_admin or (
            session.tenant_user_id == self._user_id and caller.source != GUEST_TOKEN_SOURCE
        )
        return True

    async def _read_stored_decision(self) -> dict[str, Any] | None:
        """The question a turn was waiting on; the event history does not survive a restart."""
        if self._db is None:
            return None
        try:
            return await TurnTraceStore(self._db).latest_pending_decision(
                self._session_id, self._org_id
            )
        except Exception as err:
            logger.warning("Could not read the stored question of %s: %s", self._session_id, err)
            return None

    async def _reject_draining(self) -> None:
        # Graceful drain: tell the client the pod is going away so it can
        # reconnect elsewhere, then close with the "going away" code.
        try:
            await self._ws.send_json({"type": "draining"})
        except Exception:
            pass
        await self._ws.close(code=1001, reason="server draining")

    async def _load_baseline(self) -> None:
        """Read what the sync frame reports, after the live queue is already collecting."""
        self._stored_decision = await self._read_stored_decision()
        record = await self._store.get_session(self._session_id)
        if record is not None:
            self._session = record
        self._running_turn = await self._read_running_turn()

    async def _read_running_turn(self) -> dict[str, Any] | None:
        running = _active_running_turns.get(self._session_id)
        if running is not None:
            return {
                "turn_index": running["turn_index"],
                "started_at": running["started_at"],
                "model": running.get("model") or "",
                "status": running.get("status") or "running",
                "steps": list(running.get("steps") or []),
            }
        if self._db is None:
            return None
        trace = await session_running_turn(self._db, self._session_id, self._org_id)
        if trace is None:
            return None
        return {
            "turn_index": trace.turn_index,
            "started_at": trace.started_at,
            "model": trace.tokens.get("model") or "",
            "status": trace.status,
            "steps": list(trace.tool_calls or []),
        }

    def _snapshot(self, seq_before_read: int) -> tuple[dict[str, Any], int]:
        """The sync frame and the sequence the live stream must continue after.

        Synchronous on purpose: see the class docstring.
        """
        bus, session_id = self._event_bus, self._session_id
        history, _ = bus.replay_since(session_id, 0)
        gates = pending_gates(history)
        latest = bus.latest_seq(session_id)
        in_flight_cursor = _in_flight_turn_cursor(history, latest)
        running_turn = getattr(self, "_running_turn", None)
        catch_up_from = min(seq_before_read, in_flight_cursor)
        catch_up, continuous = bus.replay_since(session_id, catch_up_from)
        frame: dict[str, Any] = {
            "type": "sync",
            "session_id": session_id,
            "session": asdict(self._session),
            # A turn paused on a person before this client connected would otherwise
            # never show its card.
            "pending_approval": gates.approval,
            "pending_decision": gates.decision or self._stored_decision,
            "running_turn": running_turn,
            # Sent back as `resume` after a reconnect so nothing published in the gap is lost.
            "last_seq": latest,
            "catch_up": (
                {"from_seq": catch_up_from, "events": [_event_frame(e) for e in catch_up]}
                if continuous and catch_up
                else None
            ),
        }
        return frame, latest

    async def _stream_events(self, queue: asyncio.Queue[SessionEvent], floor_seq: int) -> None:
        while True:
            event = await queue.get()
            if event.seq <= floor_seq:
                continue
            try:
                await self._ws.send_json(_event_frame(event))
            except Exception:
                break

    async def _receive_frames(self) -> None:
        while True:
            try:
                data = json.loads(await self._ws.receive_text())
            except WebSocketDisconnect:
                raise
            except Exception:
                continue
            if isinstance(data, dict):
                await self._dispatch(data)

    async def _dispatch(self, data: dict[str, Any]) -> None:
        frame = data.get("type")
        if frame in WRITE_FRAMES and not self._can_write:
            await self._error("Forbidden: read-only role cannot steer a session")
            return
        if frame == "share" and not self._can_share:
            await self._error("Forbidden: only the owner or an admin can share")
            return
        if frame not in self._handlers:
            if data.get("action") == "turn":
                frame = "turn"
            elif data.get("action") == "cancel":
                frame = "cancel"
            else:
                return
        await self._handlers[frame](data)

    async def _error(self, detail: str) -> None:
        await self._ws.send_json({"type": "error", "detail": detail})

    async def _forget_decisions(self) -> None:
        await _forget_pending_decisions(self._db, self._session_id, self._session.tenant_org_id)

    def _start_turn(self, prompt: str, **extra: Any) -> None:
        state = self._ws.app.state
        _spawn_background_turn(
            session_id=self._session_id,
            prompt=prompt,
            orchestrator=self._orchestrator,
            event_bus=self._event_bus,
            driver=self._driver,
            registry=self._registry,
            store=self._store,
            git_engine=getattr(state, "git_engine", None),
            db_mgr=self._db,
            author_user_id=self._user_id,
            concurrency_gate=getattr(state, "global_concurrency_gate", None),
            session_locks=getattr(state, "session_write_locks", None),
            **extra,
        )

    async def _publish_decision(self, data: dict[str, Any]) -> None:
        await self._event_bus.publish(
            SessionEvent(
                session_id=self._session_id,
                event_type=EventType.DECISION_SUBMITTED,
                payload={
                    "question_id": data.get("question_id", ""),
                    "selected_options": data.get("selected_options", []),
                    "custom_text": data.get("custom_text"),
                },
            )
        )

    async def _on_turn(self, data: dict[str, Any]) -> None:
        prompt = (data.get("content") or data.get("prompt") or "").strip()
        if not prompt:
            return
        # While a turn of this session runs (here or on another replica), the message is kept
        # until a turn takes it: steering joins the running turn at its next step rather than
        # waiting for the end of a turn that may run for an hour, a queued one runs after it.
        # Files go with a turn of their own.
        if not data.get("attachment_ids") and await self._keep_for_running_turn(prompt, data):
            return
        await self._forget_decisions()
        self._start_turn(
            prompt,
            model=data.get("model"),
            selection=_selection_of(data),
            # The submitter's identity and optimistic message id, so a collaborator's
            # instruction is attributed to them and deduped against their local entry.
            message_id=data.get("message_id"),
            attachment_service=getattr(self._ws.app.state, "attachment_service", None),
            attachment_ids=data.get("attachment_ids") or [],
        )

    async def _keep_for_running_turn(self, prompt: str, data: dict[str, Any]) -> bool:
        """Store a message sent while a turn runs; whether one did (else a turn starts now)."""
        return await hold_for_running_turn(
            session_store=self._store,
            orchestrator=self._orchestrator,
            event_bus=self._event_bus,
            db_mgr=self._db,
            org_id=self._org_id,
            session_id=self._session_id,
            prompt=prompt,
            delivery="queue" if data.get("delivery") == "queue" else "steer",
            message_id=data.get("message_id"),
            author_user_id=self._user_id,
        )

    async def _on_cancel_message(self, data: dict[str, Any]) -> None:
        """Withdraw a message no turn has taken yet."""
        message_id = str(data.get("message_id") or "")
        if not message_id:
            return
        if await QueuedMessageStore(self._db).cancel(self._org_id, self._session_id, message_id):
            self._orchestrator.withdraw_steering(self._session_id, message_id)
            await self._event_bus.publish(
                SessionEvent(
                    session_id=self._session_id,
                    event_type=EventType.MESSAGE_CANCELLED,
                    payload={"message_id": message_id},
                )
            )

    async def _on_cancel(self, data: dict[str, Any]) -> None:
        await self._forget_decisions()
        await self._orchestrator.cancel_turn(self._session_id)
        await self._event_bus.publish(
            SessionEvent(
                session_id=self._session_id,
                event_type=EventType.STATUS_CHANGED,
                payload={"status": "cancelled", "message": "Turn interrupted by user."},
            )
        )

    async def _on_resume(self, data: dict[str, Any]) -> None:
        # Replay what was published after the client's last applied event, then continue
        # live. A gap older than the retained history is reported as discontinuous so the
        # client refetches the snapshot instead of trusting an incomplete transcript.
        last_seq = int(data.get("last_seq") or 0)
        await self._event_bus.attach(self._session_id)
        replayed, continuous = self._event_bus.replay_since(self._session_id, last_seq)
        await self._ws.send_json(
            {
                "type": "replay",
                "session_id": self._session_id,
                "continuous": continuous,
                "last_seq": self._event_bus.latest_seq(self._session_id),
                "events": [_event_frame(event) for event in replayed],
            }
        )

    async def _on_answer_question(self, data: dict[str, Any]) -> None:
        question_id = data.get("question_id", "")
        if not question_id:
            return
        options = data.get("selected_options", [])
        text = data.get("custom_text")
        try:
            await self._orchestrator.submit_question_answer(
                session_id=self._session_id,
                question_id=question_id,
                selected_options=options,
                custom_text=text,
            )
        except KeyError as err:
            # No turn is waiting any more (the API restarted): the stored question is
            # answered by starting a continuation that carries the answer.
            logger.info("No live turn waits on question %s: %s", question_id, err)
            await self._continue_from_stored_question(data)
            return
        await self._publish_decision(data)
        await self._forget_decisions()

    async def _continue_from_stored_question(self, data: dict[str, Any]) -> None:
        stored = await take_stored_question(
            self._db, self._session_id, self._session.tenant_org_id, str(data.get("question_id"))
        )
        if stored is None:
            return
        await self._publish_decision(data)
        self._start_turn(
            decision_answer_prompt(
                str(stored.get("question_text") or "your question"),
                data.get("selected_options", []),
                data.get("custom_text"),
            ),
            synthetic_prompt=True,
        )

    async def _on_approval(self, data: dict[str, Any]) -> None:
        action_id = data.get("action_id", "")
        if not action_id:
            return
        try:
            await self._orchestrator.submit_human_approval(
                session_id=self._session_id,
                action_id=action_id,
                approved=bool(data.get("approved", True)),
            )
        except KeyError as err:
            logger.warning("Pending approval %s not found: %s", action_id, err)

    async def _on_share(self, data: dict[str, Any]) -> None:
        is_shared = bool(data.get("is_shared", False))
        collaborators = data.get("collaborators", [])
        record = await self._store.get_session(self._session_id)
        if not record:
            return
        await self._store.mutate_session(
            self._session_id,
            record.tenant_org_id,
            lambda current: apply_sharing(current, is_shared, collaborators),
        )
        await self._event_bus.publish(
            SessionEvent(
                session_id=self._session_id,
                event_type=EventType.SESSION_UPDATED,
                payload={"is_shared": is_shared, "collaborators": collaborators},
            )
        )

    async def _on_ping(self, data: dict[str, Any]) -> None:
        await self._ws.send_json({"type": "pong"})


_TURN_OVER_STATUSES = frozenset({"completed", "error", "cancelled"})


def _in_flight_turn_cursor(history: list[SessionEvent], latest: int) -> int:
    """The sequence just before the running turn's first event, or ``latest`` when idle.

    A turn persists its answer only when it ends, so a client connecting mid-turn needs the
    turn's events from the start to show the text streamed so far.
    """
    start: int | None = None
    for event in history:
        if event.event_type is not EventType.STATUS_CHANGED:
            continue
        status = event.payload.get("status")
        if status == "running" and start is None:
            # A turn reports "running" many times (provisioning, every step); its first
            # report after the previous turn ended is where it began.
            start = event.seq - 1
        elif status in _TURN_OVER_STATUSES and event.payload.get("final"):
            # Only the runner's own closing event counts: the orchestrator reports
            # "completed" before the answer is stored, and a client that connects in
            # between still needs the whole turn.
            start = None
    return latest if start is None else start


def _event_frame(event: SessionEvent) -> dict[str, Any]:
    return {
        "event_type": event.event_type.value,
        "session_id": event.session_id,
        "payload": event.payload,
        "timestamp_ms": event.timestamp_ms,
        # Clients track this to resume after a reconnect.
        "seq": event.seq,
    }


def _selection_of(data: dict[str, Any]) -> TurnSelection | None:
    """The composer's choice sent with the turn (an agent, or a raw model), if any."""
    mode, agent_id = data.get("mode"), data.get("agent_id")
    if mode is None and not agent_id:
        return None
    return TurnSelection(agent_id=str(agent_id) if agent_id else None, raw=mode == "direct")
