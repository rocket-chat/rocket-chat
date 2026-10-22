"""Messages a person sends while a turn runs: steering it, or queued for after it.

Kept in ``session_queued_messages`` rather than the process's memory, so a restart or another
replica loses none of them. A steering message is also handed to the running turn directly (the
fast path); the turn marks it ``taken`` when the model gets it. When a turn ends, every message
it did not take, and every queued one, is claimed at once and run as the next turns, one each, in
the order they were sent (each keeps its id, so every participant's cockpit matches it to what it
showed). A message left pending (its replica restarted) is picked up by the sweeper.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select, update

from api.db.models import SessionQueuedMessageModel
from api.services.turn_trace import session_running_turn
from specifications.interfaces.agent import SteeringMessage
from specifications.interfaces.events import EventType, SessionEvent

Delivery = Literal["steer", "queue"]
PENDING, TAKEN, STARTED, CANCELLED = "pending", "taken", "started", "cancelled"
# A pending message older than this, on a session with no running turn, is started by the sweeper.
STRANDED_AFTER_SECONDS = 60

# Without a database (development and tests): by organization, then message id.
_memory: dict[str, dict[str, SessionQueuedMessageModel]] = {}


@dataclass(frozen=True)
class QueuedMessage:
    id: str
    session_id: str
    org_id: str
    content: str
    delivery: str
    author_user_id: str | None
    created_at: int


def _plain(row: SessionQueuedMessageModel) -> QueuedMessage:
    return QueuedMessage(
        id=row.id,
        session_id=row.session_id,
        org_id=row.org_id,
        content=row.content,
        delivery=row.delivery,
        author_user_id=row.author_user_id,
        created_at=row.created_at,
    )


class QueuedMessageStore:
    def __init__(self, db_mgr: Any) -> None:
        self._db = db_mgr

    async def add(
        self,
        org_id: str,
        session_id: str,
        message_id: str,
        content: str,
        delivery: Delivery,
        author_user_id: str | None,
    ) -> QueuedMessage:
        row = SessionQueuedMessageModel(
            id=message_id,
            session_id=session_id,
            org_id=org_id,
            content=content,
            delivery=delivery,
            author_user_id=author_user_id,
            status=PENDING,
            created_at=int(time.time()),
        )
        if self._db is None:
            _memory.setdefault(org_id, {})[message_id] = row
        else:
            async with self._db.session(tenant_org_id=org_id) as session:
                session.add(row)
        return _plain(row)

    async def _move(
        self, org_id: str, session_id: str, status: str, ids: list[str] | None
    ) -> list[QueuedMessage]:
        """Atomically move the session's pending messages (or those of ``ids``) to ``status``.

        One UPDATE ... RETURNING under the row lock, so a turn ending here and the sweeper on
        another replica can never both start the same message.
        """
        if self._db is None:
            rows = [
                r
                for r in _memory.get(org_id, {}).values()
                if r.session_id == session_id
                and r.status == PENDING
                and (ids is None or r.id in ids)
            ]
            for row in rows:
                row.status = status
            return sorted((_plain(r) for r in rows), key=lambda m: (m.created_at, m.id))
        condition = [
            SessionQueuedMessageModel.session_id == session_id,
            SessionQueuedMessageModel.status == PENDING,
        ]
        if ids is not None:
            condition.append(SessionQueuedMessageModel.id.in_(ids))
        async with self._db.session(tenant_org_id=org_id) as session:
            result = await session.execute(
                update(SessionQueuedMessageModel)
                .where(*condition)
                .values(status=status)
                .returning(SessionQueuedMessageModel)
            )
            moved = [_plain(r) for r in result.scalars().all()]
        return sorted(moved, key=lambda m: (m.created_at, m.id))

    async def mark_taken(self, org_id: str, session_id: str, message_id: str) -> None:
        await self._move(org_id, session_id, TAKEN, [message_id])

    async def cancel(self, org_id: str, session_id: str, message_id: str) -> bool:
        return bool(await self._move(org_id, session_id, CANCELLED, [message_id]))

    async def claim_for_next_turn(self, org_id: str, session_id: str) -> list[QueuedMessage]:
        return await self._move(org_id, session_id, STARTED, None)

    async def pending(self, org_id: str, session_id: str) -> list[QueuedMessage]:
        if self._db is None:
            rows = [
                r
                for r in _memory.get(org_id, {}).values()
                if r.session_id == session_id and r.status == PENDING
            ]
        else:
            async with self._db.session(tenant_org_id=org_id) as session:
                result = await session.execute(
                    select(SessionQueuedMessageModel).where(
                        SessionQueuedMessageModel.session_id == session_id,
                        SessionQueuedMessageModel.status == PENDING,
                    )
                )
                rows = list(result.scalars().all())
        return sorted((_plain(r) for r in rows), key=lambda m: (m.created_at, m.id))


async def stranded_sessions(admin_db: Any, now: int | None = None) -> list[tuple[str, str]]:
    """(org, session) with a message pending for longer than ``STRANDED_AFTER_SECONDS``.

    Read on the schema-owner connection: the sweeper serves every organization.
    """
    cutoff = int(now if now is not None else time.time()) - STRANDED_AFTER_SECONDS
    if admin_db is None:
        return sorted(
            {
                (r.org_id, r.session_id)
                for rows in _memory.values()
                for r in rows.values()
                if r.status == PENDING and r.created_at <= cutoff
            }
        )
    async with admin_db.session() as session:
        result = await session.execute(
            select(SessionQueuedMessageModel.org_id, SessionQueuedMessageModel.session_id)
            .where(
                SessionQueuedMessageModel.status == PENDING,
                SessionQueuedMessageModel.created_at <= cutoff,
            )
            .distinct()
        )
        return [(str(org), str(session_id)) for org, session_id in result.all()]


async def _turn_running(
    session_store: Any, orchestrator: Any, db_manager: Any, org_id: str, session_id: str
) -> bool:
    """Whether a turn of the session runs, here or on another replica.

    With a database, the turn's own trace says so: the session's busy flag is cleared only when
    a turn ends, so one whose process died would hold every later message for a turn that does
    not exist. Without one (an in-memory deployment) the flag is all there is.
    """
    if orchestrator.is_running(session_id):
        return True
    if db_manager is not None:
        return await session_running_turn(db_manager, session_id, org_id) is not None
    record = await session_store.get_session(session_id)
    return record is not None and record.is_processing


async def hold_for_running_turn(
    *,
    session_store: Any,
    orchestrator: Any,
    event_bus: Any,
    db_mgr: Any,
    org_id: str,
    session_id: str,
    prompt: str,
    delivery: Delivery,
    message_id: str | None,
    author_user_id: str | None,
) -> bool:
    """Keep a message sent while a turn of the session runs (here or on another replica).

    Whether it was kept; ``False`` means no turn runs and the caller starts one now. A steering
    message is also handed to a turn running in this process, which takes it at its next step.
    """
    if not await _turn_running(session_store, orchestrator, db_mgr, org_id, session_id):
        return False
    kept_id = message_id or f"msg_{uuid.uuid4().hex[:16]}"
    await QueuedMessageStore(db_mgr).add(
        org_id, session_id, kept_id, prompt, delivery, author_user_id
    )
    await event_bus.publish(
        SessionEvent(
            session_id=session_id,
            event_type=EventType.MESSAGE_PENDING,
            payload={
                "message_id": kept_id,
                "delivery": delivery,
                "content": prompt,
                "author_user_id": author_user_id,
            },
        )
    )
    if delivery == "steer":
        orchestrator.steer(
            session_id,
            SteeringMessage(content=prompt, message_id=kept_id, author_user_id=author_user_id),
        )
    return True
