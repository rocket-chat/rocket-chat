"""PostgreSQL-backed SessionStore implementation conforming to SessionStoreProtocol."""

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy import CursorResult, and_, func, or_, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, defer

from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.session import (
    SessionCursor,
    SessionPage,
    SessionRecord,
    SessionSource,
    SessionStoreProtocol,
    encode_session_cursor,
)

from .models import OrganizationModel, SessionModel, TurnTraceModel, UserModel
from .session import DatabaseSessionManager

AUTO_PROVISION_ENV_FLAG = "ROCKET_AUTO_PROVISION_TENANTS"

# Canonical terminal/intermediate states of a turn trace row. A turn is inserted as
# `running` and updated in place to one of the terminal states, so the row's lifecycle is
# exactly: running -> completed | error | interrupted.
TURN_STATUS_RUNNING = "running"
TURN_STATUS_COMPLETED = "completed"
TURN_STATUS_ERROR = "error"
TURN_STATUS_INTERRUPTED = "interrupted"


def _auto_provision_tenants_enabled() -> bool:
    """Whether unknown tenant identities may be created on demand.

    Off by default: in production a tenant identity comes from the identity provider,
    and a session referencing an unknown user must fail loudly (the database enforces
    this with a foreign key). Development, E2E and CI enable it so a developer — or a
    test impersonating a tenant through headers — works without pre-seeding rows.
    """
    return os.getenv(AUTO_PROVISION_ENV_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


async def ensure_tenant_identity(session: AsyncSession, org_id: str, user_id: str) -> None:
    """Idempotently create the organization and user a tenant operation references."""
    if await session.get(OrganizationModel, org_id) is None:
        session.add(OrganizationModel(id=org_id, name=org_id, slug=org_id.replace("_", "-")))
        await session.flush()
    if await session.get(UserModel, user_id) is None:
        session.add(
            UserModel(
                id=user_id,
                org_id=org_id,
                email=f"{user_id}@auto-provisioned.local",
                name=user_id.replace("_", " ").title(),
            )
        )
        await session.flush()


@dataclass
class TurnTraceRecord:
    """A persisted turn trace row, decoupled from the ORM entity."""

    id: str
    session_id: str
    org_id: str
    turn_index: int
    started_at: int
    ended_at: int | None
    status: str
    reasoning_summary: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    diff_paths: list[str] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    tokens: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    owner_instance: str | None = None
    heartbeat_at: int | None = None
    resume_attempt: int = 0


@dataclass(frozen=True)
class ResumableTurn:
    """An interrupted turn this replica has claimed and must now resume."""

    trace_id: str
    session_id: str
    org_id: str
    resume_attempt: int


class TurnTraceStore:
    """PostgreSQL repository for durable turn traces, scoped by tenant RLS."""

    def __init__(self, manager: DatabaseSessionManager) -> None:
        self.manager = manager

    def _model_to_record(self, model: TurnTraceModel) -> TurnTraceRecord:
        return TurnTraceRecord(
            id=model.id,
            session_id=model.session_id,
            org_id=model.org_id,
            turn_index=model.turn_index,
            started_at=model.started_at,
            ended_at=model.ended_at,
            status=model.status,
            reasoning_summary=model.reasoning_summary,
            tool_calls=list(model.tool_calls or []),
            diff_paths=list(model.diff_paths or []),
            approvals=list(model.approvals or []),
            tokens=dict(model.tokens or {}),
            attachments=list(model.attachments or []),
            error=model.error,
            owner_instance=model.owner_instance,
            heartbeat_at=model.heartbeat_at,
            resume_attempt=model.resume_attempt,
        )

    def _to_model(self, trace: TurnTraceRecord) -> TurnTraceModel:
        return TurnTraceModel(
            id=trace.id,
            session_id=trace.session_id,
            org_id=trace.org_id,
            turn_index=trace.turn_index,
            started_at=trace.started_at,
            ended_at=trace.ended_at,
            status=trace.status,
            reasoning_summary=trace.reasoning_summary,
            tool_calls=trace.tool_calls,
            diff_paths=trace.diff_paths,
            approvals=trace.approvals,
            tokens=trace.tokens,
            attachments=trace.attachments,
            error=trace.error,
            owner_instance=trace.owner_instance,
            heartbeat_at=trace.heartbeat_at,
            resume_attempt=trace.resume_attempt,
        )

    async def begin(self, trace: TurnTraceRecord) -> None:
        """Insert the durable ``running`` marker a turn leaves before doing any work.

        Without this row a process death mid-turn leaves no evidence, so nothing can be
        recovered. The marker is created under the tenant RLS context of
        the turn's organization.
        """
        async with self.manager.session(tenant_org_id=trace.org_id) as session:
            session.add(self._to_model(trace))

    async def complete(self, trace: TurnTraceRecord) -> None:
        """Finalize the row begun for this turn, updating it in place.

        The terminal write is authoritative and single: when ``begin`` landed, the same
        row is updated so a turn never leaves two traces. The insert fallback covers only
        the case where the begin write was skipped (database unavailable at turn start),
        preserving "exactly one trace per turn" without a blind upsert that could clobber
        a concurrently-recovered ``interrupted`` row.
        """
        async with self.manager.session(tenant_org_id=trace.org_id) as session:
            result = await session.execute(
                update(TurnTraceModel)
                .where(TurnTraceModel.id == trace.id)
                .values(
                    ended_at=trace.ended_at,
                    status=trace.status,
                    reasoning_summary=trace.reasoning_summary,
                    tool_calls=trace.tool_calls,
                    diff_paths=trace.diff_paths,
                    approvals=trace.approvals,
                    tokens=trace.tokens,
                    attachments=trace.attachments,
                    error=trace.error,
                    # A finished turn is waiting on nobody. An interrupted one keeps its
                    # question: the person can still answer it after the restart.
                    **(
                        {}
                        if trace.status == TURN_STATUS_INTERRUPTED
                        else {"pending_decision": None}
                    ),
                )
            )
            if not cast(CursorResult[Any], result).rowcount:
                session.add(self._to_model(trace))

    async def beat(self, owner_instance: str, now: int | None = None) -> int:
        """Refresh the heartbeat of this replica's running turns (administrative connection)."""
        async with self.manager.session() as session:
            result = await session.execute(
                update(TurnTraceModel)
                .where(
                    TurnTraceModel.status == TURN_STATUS_RUNNING,
                    TurnTraceModel.owner_instance == owner_instance,
                )
                .values(heartbeat_at=now if now is not None else int(time.time()))
            )
            return int(cast(CursorResult[Any], result).rowcount or 0)

    async def running_count(self, owner_instance: str) -> int:
        """How many turns this replica is running (administrative connection)."""
        async with self.manager.session() as session:
            result = await session.execute(
                select(func.count())
                .select_from(TurnTraceModel)
                .where(
                    TurnTraceModel.status == TURN_STATUS_RUNNING,
                    TurnTraceModel.owner_instance == owner_instance,
                )
            )
            return int(result.scalar_one())

    async def mark_running_as_interrupted(self, error: str, stale_before: int | None = None) -> int:
        """Recover ``running`` rows whose process stopped serving, returning how many flipped.

        Must run on the administrative connection: RLS is FORCEd and keyed on the tenant,
        so the application role can neither see nor update another tenant's rows. With a
        single replica every ``running`` row at startup belongs to a dead process
        (``stale_before=None``). With several, only rows whose heartbeat is older than
        ``stale_before`` (or was never written) are recovered, so a replica starting up does
        not interrupt turns its healthy peers are running.
        """
        condition = [TurnTraceModel.status == TURN_STATUS_RUNNING]
        if stale_before is not None:
            condition.append(
                or_(
                    TurnTraceModel.heartbeat_at.is_(None),
                    TurnTraceModel.heartbeat_at < stale_before,
                )
            )
        async with self.manager.session() as session:
            result = await session.execute(
                update(TurnTraceModel)
                .where(*condition)
                .values(
                    status=TURN_STATUS_INTERRUPTED,
                    ended_at=int(time.time()),
                    error=error,
                )
            )
            return int(cast(CursorResult[Any], result).rowcount or 0)

    async def claim_resumable(
        self,
        instance_id: str,
        *,
        max_attempts: int,
        interrupted_after: int,
        error: str,
    ) -> list[ResumableTurn]:
        """Atomically claim interrupted turns this replica should resume (administrative connection).

        Only a turn that was interrupted *by a dead process* (``error``), recently
        (``interrupted_after``), that has not already been resumed ``max_attempts`` times and
        that the user has not since followed up on (no later turn in the session) qualifies. The
        single ``UPDATE ... RETURNING`` is the claim: of several replicas racing, exactly one
        gets each row.
        """
        later = aliased(TurnTraceModel)
        newer_turn_exists = (
            select(1)
            .where(later.session_id == TurnTraceModel.session_id)
            .where(later.started_at > TurnTraceModel.started_at)
            .exists()
        )
        async with self.manager.session() as session:
            result = await session.execute(
                update(TurnTraceModel)
                .where(
                    TurnTraceModel.status == TURN_STATUS_INTERRUPTED,
                    TurnTraceModel.error == error,
                    TurnTraceModel.resumed_by.is_(None),
                    TurnTraceModel.resume_attempt < max_attempts,
                    TurnTraceModel.ended_at >= interrupted_after,
                    ~newer_turn_exists,
                    # A turn that was waiting on a person is not replayed blindly: the question
                    # stays visible and the answer starts the continuation.
                    TurnTraceModel.pending_decision.is_(None),
                )
                .values(resumed_by=instance_id)
                .returning(
                    TurnTraceModel.id,
                    TurnTraceModel.session_id,
                    TurnTraceModel.org_id,
                    TurnTraceModel.resume_attempt,
                )
            )
            return [
                ResumableTurn(
                    trace_id=row.id,
                    session_id=row.session_id,
                    org_id=row.org_id,
                    resume_attempt=row.resume_attempt,
                )
                for row in result.all()
            ]

    async def active_running_sessions(self) -> set[str]:
        """Return the set of session IDs that currently have a turn in RUNNING status."""
        async with self.manager.session() as session:
            result = await session.execute(
                select(TurnTraceModel.session_id)
                .where(TurnTraceModel.status == TURN_STATUS_RUNNING)
                .distinct()
            )
            return {row[0] for row in result.all()}

    async def latest_turn_activity(self) -> dict[str, float]:
        """Return a mapping of session_id to latest turn activity timestamp."""
        async with self.manager.session() as session:
            result = await session.execute(
                select(
                    TurnTraceModel.session_id,
                    func.max(
                        func.coalesce(
                            TurnTraceModel.ended_at,
                            TurnTraceModel.heartbeat_at,
                            TurnTraceModel.started_at,
                        )
                    ),
                ).group_by(TurnTraceModel.session_id)
            )
            return {row[0]: float(row[1]) for row in result.all() if row[1] is not None}

    async def set_pending_decision(
        self, trace_id: str, tenant_org_id: str, decision: dict[str, Any] | None
    ) -> None:
        """Record (or clear) the question a running turn is waiting on."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            await session.execute(
                update(TurnTraceModel)
                .where(TurnTraceModel.id == trace_id)
                .values(pending_decision=decision)
            )

    async def clear_pending_decisions(self, session_id: str, tenant_org_id: str) -> None:
        """The person answered: nothing of this session is pending any more."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            await session.execute(
                update(TurnTraceModel)
                .where(
                    TurnTraceModel.session_id == session_id,
                    TurnTraceModel.pending_decision.is_not(None),
                )
                .values(pending_decision=None)
            )

    async def latest_pending_decision(
        self, session_id: str, tenant_org_id: str
    ) -> dict[str, Any] | None:
        """The unanswered question of the session's most recent turn, if it still has one."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            row = (
                await session.execute(
                    select(TurnTraceModel.pending_decision)
                    .where(
                        TurnTraceModel.session_id == session_id,
                        TurnTraceModel.pending_decision.is_not(None),
                    )
                    .order_by(TurnTraceModel.started_at.desc())
                    .limit(1)
                )
            ).first()
            return dict(row[0]) if row and row[0] else None

    async def take_pending_decision(
        self, session_id: str, tenant_org_id: str, question_id: str
    ) -> dict[str, Any] | None:
        """Atomically lock, retrieve, and clear the pending decision if matching question_id."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            result = await session.execute(
                select(TurnTraceModel)
                .where(
                    TurnTraceModel.session_id == session_id,
                    TurnTraceModel.pending_decision.is_not(None),
                )
                .order_by(TurnTraceModel.started_at.desc())
                .limit(1)
                .with_for_update()
            )
            model = result.scalars().first()
            if model is None or not model.pending_decision:
                return None
            decision = dict(model.pending_decision)
            if str(decision.get("question_id")) != question_id:
                return None
            model.pending_decision = None
            await session.commit()
            return decision

    async def record_tool_call(
        self, trace_id: str, tenant_org_id: str, tool_data: Mapping[str, Any]
    ) -> None:
        """Persist a completed tool call on the running trace so reconnecting clients see it."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            result = await session.execute(
                select(TurnTraceModel).where(TurnTraceModel.id == trace_id).with_for_update()
            )
            model = result.scalars().first()
            if model is not None:
                current_calls = list(model.tool_calls or [])
                current_calls.append(dict(tool_data))
                model.tool_calls = current_calls

    async def get(self, trace_id: str, tenant_org_id: str) -> TurnTraceRecord | None:
        """One trace by id, or ``None``."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            model = await session.get(TurnTraceModel, trace_id)
            return self._model_to_record(model) if model else None

    async def interrupted_since(self, since: int) -> list[TurnTraceRecord]:
        """Turns interrupted at or after ``since`` (administrative connection, every tenant)."""
        async with self.manager.session() as session:
            result = await session.execute(
                select(TurnTraceModel)
                .where(
                    TurnTraceModel.status == TURN_STATUS_INTERRUPTED,
                    TurnTraceModel.ended_at >= since,
                )
                .order_by(TurnTraceModel.started_at)
            )
            return [self._model_to_record(model) for model in result.scalars()]

    async def sessions_stuck_processing(self, *, older_than: int) -> list[tuple[str, str]]:
        """``(org_id, session_id)`` marked as processing while no turn of theirs runs.

        A turn clears the flag when it ends, so a process that died mid-turn leaves it set: the
        session then looks busy forever and what its owner sends is queued for a turn that does
        not exist. Administrative connection.
        """
        running = (
            select(1)
            .where(
                TurnTraceModel.session_id == SessionModel.id,
                TurnTraceModel.status == TURN_STATUS_RUNNING,
            )
            .exists()
        )
        async with self.manager.session() as session:
            result = await session.execute(
                select(SessionModel.org_id, SessionModel.id).where(
                    SessionModel.is_processing.is_(True),
                    SessionModel.updated_at < older_than,
                    ~running,
                )
            )
            return [(org_id, session_id) for org_id, session_id in result.all()]

    async def running_turn(self, session_id: str, tenant_org_id: str) -> TurnTraceRecord | None:
        """Return the active running turn trace for the session, if any."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            stmt = (
                select(TurnTraceModel)
                .where(
                    TurnTraceModel.session_id == session_id,
                    TurnTraceModel.status == TURN_STATUS_RUNNING,
                )
                .order_by(TurnTraceModel.started_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            model = result.scalars().first()
            return self._model_to_record(model) if model else None

    async def list_for_session(
        self,
        session_id: str,
        tenant_org_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[TurnTraceRecord]:
        """Return a page of traces for one session, oldest turn first."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            stmt = (
                select(TurnTraceModel)
                .where(TurnTraceModel.session_id == session_id)
                .order_by(TurnTraceModel.turn_index, TurnTraceModel.started_at)
                .limit(limit)
                .offset(offset)
            )
            result = await session.execute(stmt)
            return [self._model_to_record(model) for model in result.scalars().all()]

    async def count_for_session(self, session_id: str, tenant_org_id: str) -> int:
        """Count the traces of one session so a page can report its total."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            stmt = (
                select(func.count())
                .select_from(TurnTraceModel)
                .where(TurnTraceModel.session_id == session_id)
            )
            return int((await session.execute(stmt)).scalar_one())


class PostgresSessionStore(SessionStoreProtocol):
    """PostgreSQL session repository implementing tenant-isolated persistence."""

    def __init__(self, manager: DatabaseSessionManager) -> None:
        self.manager = manager

    def _chat_message_to_dict(self, msg: ChatMessage) -> dict[str, Any]:
        data: dict[str, Any] = {
            "role": msg.role,
            "content": msg.content,
        }
        if msg.tool_call_id is not None:
            data["tool_call_id"] = msg.tool_call_id
        if msg.tool_calls is not None:
            data["tool_calls"] = msg.tool_calls
        if msg.reasoning:
            data["reasoning"] = msg.reasoning
        if msg.author_user_id:
            data["author_user_id"] = msg.author_user_id
        if msg.created_at is not None:
            data["created_at"] = msg.created_at
        for key in ("agent_id", "agent_name", "model"):
            if getattr(msg, key) is not None:
                data[key] = getattr(msg, key)
        return data

    def _dict_to_chat_message(
        self, data: dict[str, Any], fallback_created_at: int | None = None
    ) -> ChatMessage:
        created_at = data.get("created_at")
        kwargs: dict[str, Any] = {}
        if created_at is not None:
            kwargs["created_at"] = int(created_at)
        elif fallback_created_at is not None:
            kwargs["created_at"] = int(fallback_created_at)
        return ChatMessage(
            role=data.get("role", "user"),
            content=data.get("content", ""),
            tool_calls=data.get("tool_calls"),
            tool_call_id=data.get("tool_call_id"),
            reasoning=data.get("reasoning"),
            author_user_id=data.get("author_user_id"),
            agent_id=data.get("agent_id"),
            agent_name=data.get("agent_name"),
            model=data.get("model"),
            **kwargs,
        )

    def _model_to_record(
        self, model: SessionModel, *, include_history: bool = True
    ) -> SessionRecord:
        fallback_ts = model.created_at
        history = (
            [
                self._dict_to_chat_message(d, fallback_created_at=fallback_ts)
                for d in (model.conversation_history or [])
            ]
            if include_history
            else []
        )
        try:
            status = SandboxStatus(model.sandbox_status)
        except ValueError:
            status = SandboxStatus.NON_EXISTENT

        try:
            source = SessionSource(model.source)
        except ValueError:
            source = SessionSource.WEB_UI

        meta = model.metadata_json or {}
        return SessionRecord(
            session_id=model.id,
            tenant_org_id=model.org_id,
            tenant_user_id=model.user_id,
            title=model.title,
            source=source,
            created_at=model.created_at,
            updated_at=model.updated_at,
            sandbox_status=status,
            container_image=model.container_image,
            git_repo=model.git_repo,
            git_branch=model.git_branch,
            conversation_history=history,
            metadata=meta,
            is_shared=bool(meta.get("is_shared", False)),
            collaborators=meta.get("collaborators", []),
            checklist=meta.get("checklist", []),
            is_pinned=bool(model.is_pinned),
            is_archived=bool(model.is_archived),
            is_processing=bool(model.is_processing),
        )

    async def create_session(self, record: SessionRecord) -> None:
        """Persist a new session record into PostgreSQL under the tenant RLS context."""
        async with self.manager.session(tenant_org_id=record.tenant_org_id) as session:
            if _auto_provision_tenants_enabled():
                await ensure_tenant_identity(session, record.tenant_org_id, record.tenant_user_id)
            history_data = [self._chat_message_to_dict(m) for m in record.conversation_history]
            model = SessionModel(
                id=record.session_id,
                org_id=record.tenant_org_id,
                user_id=record.tenant_user_id,
                title=record.title,
                source=record.source.value,
                sandbox_status=record.sandbox_status.value,
                container_image=record.container_image,
                git_repo=record.git_repo,
                git_branch=record.git_branch,
                conversation_history=history_data,
                metadata_json=record.metadata,
                created_at=record.created_at,
                updated_at=record.updated_at,
                is_pinned=record.is_pinned,
                is_archived=record.is_archived,
                is_processing=record.is_processing,
            )
            session.add(model)

    async def get_session(
        self, session_id: str, tenant_org_id: str | None = None
    ) -> SessionRecord | None:
        """Retrieve a session by its unique ID, filtered by Row-Level Security."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            stmt = select(SessionModel).where(SessionModel.id == session_id)
            if tenant_org_id and session.bind and session.bind.dialect.name != "postgresql":
                # For SQLite / non-postgres dialects, manually filter by org_id
                stmt = stmt.where(SessionModel.org_id == tenant_org_id)

            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return None
            return self._model_to_record(model)

    def _write_record(self, model: SessionModel, record: SessionRecord) -> None:
        model.title = record.title
        model.sandbox_status = record.sandbox_status.value
        model.container_image = record.container_image
        model.git_repo = record.git_repo
        model.git_branch = record.git_branch
        model.conversation_history = [
            self._chat_message_to_dict(m) for m in record.conversation_history
        ]
        model.metadata_json = record.metadata
        model.is_pinned = record.is_pinned
        model.is_archived = record.is_archived
        model.is_processing = record.is_processing
        model.updated_at = int(time.time())

    async def update_session(self, record: SessionRecord) -> None:
        """Update session conversation history and status."""
        async with self.manager.session(tenant_org_id=record.tenant_org_id) as session:
            stmt = select(SessionModel).where(SessionModel.id == record.session_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model:
                self._write_record(model, record)

    async def mutate_session(
        self,
        session_id: str,
        tenant_org_id: str,
        mutate: Callable[[SessionRecord], None],
    ) -> SessionRecord | None:
        """Read, change and write one session under a row lock, so writers cannot overlap."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            stmt = select(SessionModel).where(SessionModel.id == session_id).with_for_update()
            model = (await session.execute(stmt)).scalar_one_or_none()
            if model is None:
                return None
            record = self._model_to_record(model)
            mutate(record)
            self._write_record(model, record)
            return record

    async def list_sessions(
        self,
        tenant_org_id: str,
        tenant_user_id: str | None = None,
        limit: int = 50,
    ) -> list[SessionRecord]:
        """List sessions filtered by tenant organization and optional user ID."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            stmt = (
                select(SessionModel)
                .options(defer(SessionModel.conversation_history))
                .where(SessionModel.org_id == tenant_org_id)
            )
            if tenant_user_id:
                stmt = stmt.where(SessionModel.user_id == tenant_user_id)
            stmt = stmt.order_by(SessionModel.updated_at.desc()).limit(limit)

            result = await session.execute(stmt)
            models = result.scalars().all()
            return [self._model_to_record(m, include_history=False) for m in models]

    async def list_sessions_page(
        self,
        tenant_org_id: str,
        *,
        tenant_user_id: str | None = None,
        visible_to_user_id: str | None = None,
        include_archived: bool = False,
        query: str | None = None,
        limit: int = 50,
        cursor: SessionCursor | None = None,
    ) -> SessionPage:
        """List one keyset page of sessions, newest ``(updated_at, session_id)`` first.

        The cursor predicate uses the tuple ``(updated_at, session_id)`` rather than
        ``OFFSET``: offsets skip or duplicate rows when sessions are created between
        page fetches, which is exactly the failure a busy sidebar must not have.
        """
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            stmt = (
                select(SessionModel)
                .options(defer(SessionModel.conversation_history))
                .where(SessionModel.org_id == tenant_org_id)
            )
            if tenant_user_id:
                stmt = stmt.where(SessionModel.user_id == tenant_user_id)
            if not include_archived:
                stmt = stmt.where(SessionModel.is_archived.is_(False))
            if query:
                stmt = stmt.where(SessionModel.title.ilike(f"%{query}%"))
            if visible_to_user_id is not None:
                metadata = sql_cast(SessionModel.metadata_json, JSONB)
                stmt = stmt.where(
                    or_(
                        SessionModel.user_id == visible_to_user_id,
                        metadata["is_shared"].as_boolean().is_(True),
                        metadata["collaborators"].contains([visible_to_user_id]),
                    )
                )
            if cursor is not None:
                stmt = stmt.where(
                    or_(
                        SessionModel.updated_at < cursor.updated_at,
                        and_(
                            SessionModel.updated_at == cursor.updated_at,
                            SessionModel.id < cursor.session_id,
                        ),
                    )
                )
            stmt = stmt.order_by(SessionModel.updated_at.desc(), SessionModel.id.desc()).limit(
                limit + 1
            )

            models = list((await session.execute(stmt)).scalars().all())

            next_cursor: str | None = None
            if len(models) > limit:
                last = models[limit - 1]
                next_cursor = encode_session_cursor(
                    SessionCursor(updated_at=int(last.updated_at), session_id=last.id)
                )
                models = models[:limit]
            return SessionPage(
                records=[self._model_to_record(m, include_history=False) for m in models],
                next_cursor=next_cursor,
            )

    async def set_session_flags(
        self,
        session_id: str,
        tenant_org_id: str,
        *,
        is_pinned: bool | None = None,
        is_archived: bool | None = None,
    ) -> SessionRecord | None:
        """Update the pin/archive flags of one session in place."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            result = await session.execute(
                select(SessionModel).where(SessionModel.id == session_id)
            )
            model = result.scalar_one_or_none()
            if model is None:
                return None
            if is_pinned is not None:
                model.is_pinned = is_pinned
            if is_archived is not None:
                model.is_archived = is_archived
            model.updated_at = int(time.time())
            await session.flush()
            return self._model_to_record(model)

    async def delete_session(self, session_id: str, tenant_org_id: str | None = None) -> bool:
        """Delete a session by its unique ID."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            stmt = select(SessionModel).where(SessionModel.id == session_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return False
            await session.delete(model)
            return True

    async def clear_all(self, tenant_org_id: str | None = None) -> None:
        """Purge all sessions from the database store."""
        async with self.manager.session(tenant_org_id=tenant_org_id) as session:
            stmt = select(SessionModel)
            if tenant_org_id and session.bind and session.bind.dialect.name != "postgresql":
                stmt = stmt.where(SessionModel.org_id == tenant_org_id)
            result = await session.execute(stmt)
            models = result.scalars().all()
            for m in models:
                await session.delete(m)
