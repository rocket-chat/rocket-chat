"""A question a turn is waiting on survives an API restart (the cockpit shows it again).

Real PostgreSQL. The question used to live only in a replica's in-memory event history, so a
deploy while a person was deciding made it vanish and the turn could not be continued.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.db.session import DatabaseSessionManager, current_tenant_org_id
from api.db.store import PostgresSessionStore, TurnTraceStore
from api.events import AsyncIOEventBus
from api.services import shutdown
from api.services.turn_resume import resume_interrupted_turns
from api.services.turn_trace import (
    TURN_STATUS_COMPLETED,
    TurnTraceAccumulator,
    begin_turn_trace,
    recover_interrupted_turns,
)
from api.turns.runner import execute_turn_background
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import ChatMessage, LLMGatewayProtocol, StreamChunk
from specifications.interfaces.session import SessionRecord

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")
ADMIN_DB_URL = os.getenv("ADMIN_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not (TEST_DB_URL and ADMIN_DB_URL), reason="TEST/ADMIN_DATABASE_URL not configured"
)

QUESTION = {
    "question_id": "q_1",
    "question_text": "Which repository should I clone?",
    "options": ["a/one", "b/two"],
    "is_multi_select": False,
    "default_recommended_option": "a/one",
}


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


@pytest.fixture(autouse=True)
def _not_shutting_down() -> AsyncIterator[None]:
    shutdown.reset_for_tests()
    yield  # type: ignore[misc]
    shutdown.reset_for_tests()


class _Tenant:
    def __init__(self, app_db: DatabaseSessionManager) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.org_id = f"org_dec_{suffix}"
        self.user_id = f"user_dec_{suffix}"
        self.session_id = f"sess_dec_{suffix}"
        self.store = PostgresSessionStore(app_db)
        self.app_db = app_db

    async def create_session(self) -> None:
        await self.store.create_session(
            SessionRecord(
                session_id=self.session_id,
                tenant_org_id=self.org_id,
                tenant_user_id=self.user_id,
                title="Decision",
            )
        )

    async def waiting_turn(self) -> TurnTraceAccumulator:
        trace = TurnTraceAccumulator(session_id=self.session_id, org_id=self.org_id, turn_index=1)
        await begin_turn_trace(self.app_db, trace)
        await TurnTraceStore(self.app_db).set_pending_decision(
            trace.trace_id, self.org_id, QUESTION
        )
        return trace


@pytest.fixture
async def tenant(app_db: DatabaseSessionManager) -> _Tenant:
    tenant = _Tenant(app_db)
    await tenant.create_session()
    return tenant


async def test_the_question_is_stored_found_and_cleared(
    tenant: _Tenant, app_db: DatabaseSessionManager
) -> None:
    store = TurnTraceStore(app_db)
    assert await store.latest_pending_decision(tenant.session_id, tenant.org_id) is None

    await tenant.waiting_turn()
    assert await store.latest_pending_decision(tenant.session_id, tenant.org_id) == QUESTION

    await store.clear_pending_decisions(tenant.session_id, tenant.org_id)
    assert await store.latest_pending_decision(tenant.session_id, tenant.org_id) is None


async def test_a_question_is_private_to_its_organization(
    tenant: _Tenant, app_db: DatabaseSessionManager
) -> None:
    await tenant.waiting_turn()

    assert (
        await TurnTraceStore(app_db).latest_pending_decision(tenant.session_id, "org_other") is None
    )


async def test_finishing_a_turn_clears_its_question_but_an_interruption_keeps_it(
    tenant: _Tenant, app_db: DatabaseSessionManager
) -> None:
    store = TurnTraceStore(app_db)
    trace = await tenant.waiting_turn()

    await store.complete(trace.finish(status="interrupted", error="restart"))
    assert await store.latest_pending_decision(tenant.session_id, tenant.org_id) == QUESTION

    await store.complete(trace.finish(status=TURN_STATUS_COMPLETED))
    assert await store.latest_pending_decision(tenant.session_id, tenant.org_id) is None


async def test_a_turn_that_was_waiting_on_a_person_is_not_resumed_blindly(
    tenant: _Tenant, app_db: DatabaseSessionManager, admin_db: DatabaseSessionManager
) -> None:
    await tenant.waiting_turn()
    await recover_interrupted_turns(admin_db)  # what startup does after a restart
    claimed: list[Any] = []

    async def collect(turn: Any) -> None:
        claimed.append(turn)

    await resume_interrupted_turns(admin_db, app_db, collect)

    assert [t for t in claimed if t.session_id == tenant.session_id] == []
    stored = await TurnTraceStore(app_db).latest_pending_decision(tenant.session_id, tenant.org_id)
    assert stored == QUESTION, "recovery marks the turn interrupted but keeps the question"


class _AsksThenWaits(LLMGatewayProtocol):
    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(
            tool_call_delta={
                "index": 0,
                "id": "call_q",
                "function": {
                    "name": "ask_question",
                    "arguments": json.dumps(
                        {
                            "question": QUESTION["question_text"],
                            "options": QUESTION["options"],
                            "default_recommended_option": QUESTION["default_recommended_option"],
                        }
                    ),
                },
            }
        )

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


async def test_a_restart_while_a_person_is_deciding_keeps_the_turn_interrupted_with_its_question(
    tenant: _Tenant, app_db: DatabaseSessionManager, admin_db: DatabaseSessionManager
) -> None:
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id=tenant.session_id)
    token = current_tenant_org_id.set(tenant.org_id)
    try:
        record = await tenant.store.get_session(tenant.session_id)
        assert record is not None
        record.conversation_history.append(ChatMessage(role="user", content="Tour the repo"))
        await tenant.store.update_session(record)
        turn = asyncio.create_task(
            execute_turn_background(
                session_id=tenant.session_id,
                prompt="Tour the repo",
                orchestrator=AsyncReActOrchestrator(gateway=_AsksThenWaits(), registry=registry),
                event_bus=AsyncIOEventBus(),
                driver=driver,
                registry=registry,
                store=tenant.store,
                db_mgr=app_db,
            )
        )
        stored = None
        for _ in range(100):
            stored = await TurnTraceStore(app_db).latest_pending_decision(
                tenant.session_id, tenant.org_id
            )
            if stored:
                break
            await asyncio.sleep(0.1)
        assert stored is not None and stored["question_text"] == QUESTION["question_text"]

        shutdown.begin_shutdown()  # the pod is being replaced
        turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
    finally:
        current_tenant_org_id.reset(token)

    traces = await TurnTraceStore(app_db).list_for_session(tenant.session_id, tenant.org_id)
    assert [t.status for t in traces] == ["interrupted"]
    assert (
        await TurnTraceStore(app_db).latest_pending_decision(tenant.session_id, tenant.org_id)
        is not None
    )
