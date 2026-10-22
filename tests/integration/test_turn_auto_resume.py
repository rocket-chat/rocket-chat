"""A turn interrupted by a restart continues instead of dying.

Real PostgreSQL: the atomic claim, its limits (attempts, age, follow-up messages, org
opt-out) and a full resumed turn through the orchestrator.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.db.session import DatabaseSessionManager, current_tenant_org_id
from api.db.store import PostgresSessionStore, ResumableTurn, TurnTraceStore
from api.events import AsyncIOEventBus
from api.services.settings import save_org_config
from api.services.turn_resume import RESUME_NOTICE, resume_interrupted_turns
from api.services.turn_trace import (
    INTERRUPTED_TURN_ERROR,
    TurnTraceAccumulator,
    begin_turn_trace,
    recover_interrupted_turns,
)
from api.turns.runner import execute_turn_background
from sandbox_driver.fake_driver import FakeSandboxDriver
from sqlalchemy import text

from specifications.interfaces.llm import ChatMessage, LLMGatewayProtocol, StreamChunk
from specifications.interfaces.session import SessionRecord

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")
ADMIN_DB_URL = os.getenv("ADMIN_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not (TEST_DB_URL and ADMIN_DB_URL), reason="TEST/ADMIN_DATABASE_URL not configured"
)


@pytest.fixture
async def app_db() -> AsyncIterator[DatabaseSessionManager]:
    manager = DatabaseSessionManager(TEST_DB_URL)
    yield manager
    await manager.close()


@pytest.fixture
async def admin_db() -> AsyncIterator[DatabaseSessionManager]:
    manager = DatabaseSessionManager(ADMIN_DB_URL)
    yield manager
    await manager.close()


class _Tenant:
    def __init__(self, app_db: DatabaseSessionManager) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.org_id = f"org_res_{suffix}"
        self.user_id = f"user_res_{suffix}"
        self.session_id = f"sess_res_{suffix}"
        self.store = PostgresSessionStore(app_db)
        self.app_db = app_db

    async def create_session(self) -> None:
        await self.store.create_session(
            SessionRecord(
                session_id=self.session_id,
                tenant_org_id=self.org_id,
                tenant_user_id=self.user_id,
                title="Resume",
            )
        )

    async def interrupted_turn(
        self, admin_db: DatabaseSessionManager, *, resume_attempt: int = 0
    ) -> TurnTraceAccumulator:
        trace = TurnTraceAccumulator(
            session_id=self.session_id,
            org_id=self.org_id,
            turn_index=1,
            resume_attempt=resume_attempt,
        )
        await begin_turn_trace(self.app_db, trace)
        await recover_interrupted_turns(admin_db)
        return trace


@pytest.fixture
async def tenant(app_db: DatabaseSessionManager) -> _Tenant:
    tenant = _Tenant(app_db)
    await tenant.create_session()
    return tenant


async def _resume_all(
    admin_db: DatabaseSessionManager, app_db: DatabaseSessionManager
) -> list[ResumableTurn]:
    claimed: list[ResumableTurn] = []

    async def collect(turn: ResumableTurn) -> None:
        claimed.append(turn)

    await resume_interrupted_turns(admin_db, app_db, collect)
    return claimed


async def test_an_interrupted_turn_is_claimed_once(
    tenant: _Tenant, app_db: DatabaseSessionManager, admin_db: DatabaseSessionManager
) -> None:
    trace = await tenant.interrupted_turn(admin_db)

    mine = [t for t in await _resume_all(admin_db, app_db) if t.session_id == tenant.session_id]
    assert [(t.trace_id, t.org_id, t.resume_attempt) for t in mine] == [
        (trace.trace_id, tenant.org_id, 0)
    ]
    assert [
        t for t in await _resume_all(admin_db, app_db) if t.session_id == tenant.session_id
    ] == []


async def test_replicas_racing_for_a_turn_resolve_to_one_winner(
    tenant: _Tenant, admin_db: DatabaseSessionManager
) -> None:
    await tenant.interrupted_turn(admin_db)

    async def claim(instance: str) -> list[ResumableTurn]:
        return await TurnTraceStore(admin_db).claim_resumable(
            instance, max_attempts=1, interrupted_after=0, error=INTERRUPTED_TURN_ERROR
        )

    results = await asyncio.gather(*(claim(f"replica-{n}") for n in range(8)))
    winners = [r for batch in results for r in batch if r.session_id == tenant.session_id]
    assert len(winners) == 1


async def test_a_follow_up_message_cancels_the_resume(
    tenant: _Tenant, app_db: DatabaseSessionManager, admin_db: DatabaseSessionManager
) -> None:
    await tenant.interrupted_turn(admin_db)
    follow_up = TurnTraceAccumulator(
        session_id=tenant.session_id,
        org_id=tenant.org_id,
        turn_index=2,
        started_at=int(time.time()) + 5,
    )
    await begin_turn_trace(app_db, follow_up)

    assert [
        t for t in await _resume_all(admin_db, app_db) if t.session_id == tenant.session_id
    ] == []


async def test_a_task_is_resumed_a_bounded_number_of_times(
    tenant: _Tenant, app_db: DatabaseSessionManager, admin_db: DatabaseSessionManager
) -> None:
    await tenant.interrupted_turn(admin_db, resume_attempt=2)  # already resumed twice

    assert [
        t for t in await _resume_all(admin_db, app_db) if t.session_id == tenant.session_id
    ] == []


async def test_a_stale_interruption_is_left_for_the_user(
    tenant: _Tenant, app_db: DatabaseSessionManager, admin_db: DatabaseSessionManager
) -> None:
    trace = await tenant.interrupted_turn(admin_db)
    async with admin_db.session() as session:
        await session.execute(
            text("UPDATE turn_traces SET ended_at = ended_at - 7200 WHERE id = :id"),
            {"id": trace.trace_id},
        )

    assert [
        t for t in await _resume_all(admin_db, app_db) if t.session_id == tenant.session_id
    ] == []


@pytest.mark.setting("governance.auto_resume_turns")
async def test_an_organization_can_opt_out(
    tenant: _Tenant, app_db: DatabaseSessionManager, admin_db: DatabaseSessionManager
) -> None:
    await save_org_config(app_db, tenant.org_id, "governance", {"auto_resume_turns": False}, [])
    await tenant.interrupted_turn(admin_db)

    assert [
        t for t in await _resume_all(admin_db, app_db) if t.session_id == tenant.session_id
    ] == []


async def test_the_environment_can_disable_resume(
    tenant: _Tenant,
    app_db: DatabaseSessionManager,
    admin_db: DatabaseSessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROCKET_TURN_AUTO_RESUME_ATTEMPTS", "0")
    await tenant.interrupted_turn(admin_db)

    assert await _resume_all(admin_db, app_db) == []


class _RecordingGateway(LLMGatewayProtocol):
    def __init__(self) -> None:
        self.seen: list[list[Any]] = []

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        self.seen.append(list(request.messages))
        yield StreamChunk(text_delta="Picked the task back up.")

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


async def test_a_resumed_turn_continues_without_a_phantom_user_message(
    tenant: _Tenant, app_db: DatabaseSessionManager, admin_db: DatabaseSessionManager
) -> None:
    gateway = _RecordingGateway()
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id=tenant.session_id)
    token = current_tenant_org_id.set(tenant.org_id)
    try:
        record = await tenant.store.get_session(tenant.session_id)
        assert record is not None
        record.conversation_history.append(ChatMessage(role="user", content="Refactor the parser"))
        await tenant.store.update_session(record)
        interrupted = await tenant.interrupted_turn(admin_db)
        await asyncio.wait_for(
            execute_turn_background(
                session_id=tenant.session_id,
                prompt=RESUME_NOTICE,
                orchestrator=AsyncReActOrchestrator(gateway=gateway, registry=registry),
                event_bus=AsyncIOEventBus(),
                driver=driver,
                registry=registry,
                store=tenant.store,
                db_mgr=app_db,
                resume_attempt=1,
            ),
            timeout=30,
        )
        history = (await tenant.store.get_session(tenant.session_id)).conversation_history  # type: ignore[union-attr]
    finally:
        current_tenant_org_id.reset(token)

    assert RESUME_NOTICE not in [m.content for m in history if m.role == "user"]
    assert any(RESUME_NOTICE in str(m.content) for m in gateway.seen[0]), "the model is told"
    traces = await TurnTraceStore(app_db).list_for_session(tenant.session_id, tenant.org_id)
    by_id = {t.id: t for t in traces}
    assert by_id[interrupted.trace_id].status == "interrupted"
    resumed = next(t for t in traces if t.id != interrupted.trace_id)
    assert (resumed.status, resumed.resume_attempt) == ("completed", 1)


async def test_the_saved_work_of_interrupted_turns_reaches_the_session_and_frees_it(
    tenant: _Tenant, app_db: DatabaseSessionManager, admin_db: DatabaseSessionManager
) -> None:
    """A real interrupted turn: its finished calls are shown and the session is not left busy."""
    from api.services.interrupted_work import clear_stuck_processing, save_interrupted_turns
    from api.services.transcript import InMemoryTranscriptStore

    def busy(record: SessionRecord) -> None:
        record.is_processing = True

    await tenant.store.mutate_session(tenant.session_id, tenant.org_id, busy)
    trace = TurnTraceAccumulator(
        session_id=tenant.session_id, org_id=tenant.org_id, turn_index=1, resume_attempt=0
    )
    await begin_turn_trace(app_db, trace)
    await TurnTraceStore(app_db).record_tool_call(
        trace.trace_id,
        tenant.org_id,
        {"id": "c1", "name": "file_read", "arguments": {}, "result": "ok", "status": "completed"},
    )
    await recover_interrupted_turns(admin_db)

    saved = await save_interrupted_turns(admin_db, tenant.store, InMemoryTranscriptStore())
    again = await save_interrupted_turns(admin_db, tenant.store, InMemoryTranscriptStore())
    await clear_stuck_processing(admin_db, tenant.store)

    record = await tenant.store.get_session(tenant.session_id, tenant.org_id)
    assert record is not None
    assert saved >= 1 and again == 0, "saved once per interrupted turn"
    assert record.is_processing is False
    assert [m.role for m in record.conversation_history] == ["assistant", "system"]
    assert (record.conversation_history[0].tool_calls or [])[0]["id"] == "c1"
