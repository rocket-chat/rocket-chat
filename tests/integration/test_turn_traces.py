"""Integration tests for durable turn traces.

Covers the three contracts the feature ships with: a completed turn persists exactly one
trace carrying its tool calls and approval gates, the rows are invisible across tenants,
and the cockpit's turn inspector can page through them.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.db.session import DatabaseSessionManager, current_tenant_org_id
from api.db.store import PostgresSessionStore, TurnTraceRecord, TurnTraceStore
from api.events import AsyncIOEventBus
from api.main import app
from api.routers.turns import router as turns_router
from api.services.turn_trace import (
    INTERRUPTED_TURN_ERROR,
    TURN_STATUS_INTERRUPTED,
    TURN_STATUS_RUNNING,
    TurnTraceAccumulator,
    begin_turn_trace,
    recover_interrupted_turns,
)
from api.turns.runner import execute_turn_background
from httpx import ASGITransport, AsyncClient
from sandbox_driver.fake_driver import FakeSandboxDriver
from sqlalchemy import text

from specifications.interfaces.events import EventType
from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk
from specifications.interfaces.session import SessionRecord

ADMIN_DB_URL = os.getenv("ADMIN_DATABASE_URL", "")

HEADERS = {
    "X-Tenant-Org-Id": "default_org",
    "X-Tenant-User-Id": "dev_user",
    "X-User-Role": "developer",
}

# The tenant policy migration 003 creates. CI provisions the schema from SQLAlchemy
# metadata and never runs Alembic, so the isolation test applies it when it is absent;
# tests/unit/test_turn_trace.py asserts the migration declares exactly this policy.
_RLS_STATEMENTS = (
    "ALTER TABLE turn_traces ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE turn_traces FORCE ROW LEVEL SECURITY;",
    """
    CREATE POLICY tenant_isolation_turn_traces ON turn_traces
        FOR ALL
        USING (org_id = current_setting('app.current_org_id', true));
    """,
)


@pytest.fixture
async def db_manager(require_database: Callable[[Exception, str], None]) -> AsyncIterator[Any]:
    """Application-role manager: superusers bypass RLS by design, so it must be rocket_app."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured")
    manager = DatabaseSessionManager(url)
    try:
        async with manager.session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as err:
        require_database(err, url)

    yield manager
    await manager.close()


@pytest.fixture
async def admin_db_manager(
    require_database: Callable[[Exception, str], None],
) -> AsyncIterator[Any]:
    """Administrative manager for the cross-tenant startup sweep.

    RLS is FORCEd, so recovering every tenant's stale `running` rows requires the
    schema-owner connection (`ADMIN_DATABASE_URL`), exactly as the startup snippet uses.
    """
    url = ADMIN_DB_URL or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("ADMIN_DATABASE_URL/TEST_DATABASE_URL not configured")
    manager = DatabaseSessionManager(url)
    try:
        async with manager.session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as err:
        require_database(err, url)

    yield manager
    await manager.close()


async def _ensure_turn_trace_rls() -> None:
    """Apply the tenant policy when the schema was created from metadata alone."""
    if not ADMIN_DB_URL:
        return
    manager = DatabaseSessionManager(ADMIN_DB_URL)
    try:
        async with manager.session() as session:
            existing = (
                await session.execute(
                    text(
                        "SELECT 1 FROM pg_policies WHERE tablename = 'turn_traces' "
                        "AND policyname = 'tenant_isolation_turn_traces'"
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return
            for statement in _RLS_STATEMENTS:
                await session.execute(text(statement))
    finally:
        await manager.close()


class ScriptedGateway(LLMGatewayProtocol):
    """Replays a fixed script of tool calls, then a final textual answer."""

    def __init__(self, script: list[dict[str, Any]], final: str = "Turn complete.") -> None:
        self.script = script
        self.final = final
        self.step = 0

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        if self.step < len(self.script):
            call = self.script[self.step]
            self.step += 1
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call_{self.step}",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("arguments", {})),
                    },
                }
            )
            return
        yield StreamChunk(text_delta=self.final)

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        pass


async def _await_approval_action_id(bus: AsyncIOEventBus, session_id: str) -> str:
    """Wait for the turn's approval gate using the bus replay window, not a subscription."""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        events, _ = bus.replay_since(session_id, 0)
        for event in events:
            if event.event_type is EventType.APPROVAL_REQUIRED:
                return str(event.payload["action_id"])
        await asyncio.sleep(0.01)
    raise AssertionError("the turn never requested approval")


@pytest.mark.asyncio
async def test_completed_turn_persists_exactly_one_trace(db_manager: Any) -> None:
    """A turn that used a tool and cleared an approval gate leaves one complete trace."""
    session_id = f"sess_trace_{uuid.uuid4().hex[:8]}"
    store = PostgresSessionStore(db_manager)
    await store.create_session(
        SessionRecord(
            session_id=session_id,
            tenant_org_id="default_org",
            tenant_user_id="dev_user",
            title="Trace persistence",
        )
    )

    gateway = ScriptedGateway(
        script=[
            {
                "name": "request_approval",
                "arguments": {
                    "action_type": "git_revert_hard",
                    "description": "Discard uncommitted work",
                    "risk_level": "critical",
                },
            },
            {"name": "bash_exec", "arguments": {"command": "echo trace", "cwd": "/workspace"}},
        ]
    )
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver, session_id=session_id)
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)
    bus = AsyncIOEventBus()

    # A request binds the tenant through middleware; a directly-driven turn must too.
    token = current_tenant_org_id.set("default_org")
    try:
        turn = asyncio.create_task(
            execute_turn_background(
                session_id=session_id,
                prompt="Revert the workspace and re-run the suite",
                orchestrator=orchestrator,
                event_bus=bus,
                driver=driver,
                registry=registry,
                store=store,
                db_mgr=db_manager,
            )
        )
        action_id = await _await_approval_action_id(bus, session_id)
        await orchestrator.submit_human_approval(
            session_id=session_id, action_id=action_id, approved=True
        )
        await asyncio.wait_for(turn, timeout=30)
    finally:
        current_tenant_org_id.reset(token)

    traces = await TurnTraceStore(db_manager).list_for_session(session_id, "default_org")

    assert len(traces) == 1, "a completed turn must persist exactly one trace"
    trace = traces[0]
    assert trace.status == "completed"
    assert trace.turn_index == 1
    assert trace.ended_at >= trace.started_at
    assert [call["name"] for call in trace.tool_calls] == ["request_approval", "bash_exec"]
    assert trace.tool_calls[1]["status"] == "completed"
    assert trace.approvals == [
        {
            "action_id": action_id,
            "action_type": "git_revert_hard",
            "command_or_path": "Discard uncommitted work",
            "risk_level": "critical",
        }
    ]
    assert trace.error is None


@pytest.mark.asyncio
async def test_turn_traces_are_isolated_between_organizations(db_manager: Any) -> None:
    """Row-level security must keep one tenant's traces invisible to another."""
    await _ensure_turn_trace_rls()

    suffix = uuid.uuid4().hex[:6]
    org_a, org_b = f"org_a_{suffix}", f"org_b_{suffix}"
    user_a, user_b = f"user_a_{suffix}", f"user_b_{suffix}"
    session_a, session_b = f"sess_a_{suffix}", f"sess_b_{suffix}"

    sessions = PostgresSessionStore(db_manager)
    for org_id, user_id, session_id in ((org_a, user_a, session_a), (org_b, user_b, session_b)):
        await sessions.create_session(
            SessionRecord(
                session_id=session_id,
                tenant_org_id=org_id,
                tenant_user_id=user_id,
                title="RLS isolation",
            )
        )

    traces = TurnTraceStore(db_manager)
    await traces.complete(
        TurnTraceRecord(
            id=f"trace_{uuid.uuid4().hex[:16]}",
            session_id=session_a,
            org_id=org_a,
            turn_index=1,
            started_at=1_700_000_000,
            ended_at=1_700_000_010,
            status="completed",
            tool_calls=[{"name": "bash_exec", "status": "completed"}],
        )
    )

    assert len(await traces.list_for_session(session_a, org_a)) == 1
    assert await traces.count_for_session(session_a, org_a) == 1

    # Org B addressing org A's session by primary key must see nothing at all.
    assert await traces.list_for_session(session_a, org_b) == []
    assert await traces.count_for_session(session_a, org_b) == 0
    assert await sessions.get_session(session_a, tenant_org_id=org_b) is None


@pytest.mark.asyncio
async def test_turns_endpoint_paginates_and_scopes_by_tenant(db_manager: Any) -> None:
    """``GET /v1/sessions/{id}/turns`` pages oldest-first and hides other tenants' rows."""
    await _ensure_turn_trace_rls()

    session_id = f"sess_page_{uuid.uuid4().hex[:8]}"
    store = PostgresSessionStore(db_manager)
    await store.create_session(
        SessionRecord(
            session_id=session_id,
            tenant_org_id="default_org",
            tenant_user_id="dev_user",
            title="Turn inspector",
        )
    )

    traces = TurnTraceStore(db_manager)
    for turn_index in (1, 2, 3):
        await traces.complete(
            TurnTraceRecord(
                id=f"trace_{uuid.uuid4().hex[:16]}",
                session_id=session_id,
                org_id="default_org",
                turn_index=turn_index,
                started_at=1_700_000_000 + turn_index,
                ended_at=1_700_000_100 + turn_index,
                status="completed",
                diff_paths=[f"src/file_{turn_index}.py"],
                attachments=[{"id": "att_1", "name": "log.txt", "mime": "text/plain", "size": 12}]
                if turn_index == 1
                else [],
            )
        )

    app.state.db_manager = db_manager
    app.state.session_store = store
    if not any(
        getattr(route, "path", "") == "/v1/sessions/{session_id}/turns" for route in app.routes
    ):
        # api.main wires this router separately; until that lands the endpoint would 404.
        app.include_router(turns_router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first_page = await client.get(
            f"/v1/sessions/{session_id}/turns?limit=2&offset=0", headers=HEADERS
        )
        assert first_page.status_code == 200
        page = first_page.json()
        assert page["total"] == 3
        assert [turn["turn_index"] for turn in page["turns"]] == [1, 2]
        assert page["turns"][0]["diff_paths"] == ["src/file_1.py"]
        assert page["turns"][0]["attachments"] == [
            {"id": "att_1", "name": "log.txt", "mime": "text/plain", "size": 12}
        ]
        assert page["turns"][1]["attachments"] == []

        second_page = await client.get(
            f"/v1/sessions/{session_id}/turns?limit=2&offset=2", headers=HEADERS
        )
        assert [turn["turn_index"] for turn in second_page.json()["turns"]] == [3]

        invalid = await client.get(f"/v1/sessions/{session_id}/turns?limit=0", headers=HEADERS)
        assert invalid.status_code == 422

        other_tenant = await client.get(
            f"/v1/sessions/{session_id}/turns",
            headers={
                "X-Tenant-Org-Id": "test_org",
                "X-Tenant-User-Id": "test_user",
                "X-User-Role": "developer",
            },
        )
        assert other_tenant.status_code == 404


@pytest.mark.asyncio
async def test_interrupted_turn_is_recovered_and_reported(
    db_manager: Any, admin_db_manager: Any
) -> None:
    """A turn whose process died mid-flight is marked interrupted and readable via the API."""
    await _ensure_turn_trace_rls()

    suffix = uuid.uuid4().hex[:6]
    org_id, user_id = f"org_rec_{suffix}", f"user_rec_{suffix}"
    session_id = f"sess_rec_{suffix}"
    store = PostgresSessionStore(db_manager)
    await store.create_session(
        SessionRecord(
            session_id=session_id,
            tenant_org_id=org_id,
            tenant_user_id=user_id,
            title="Interrupted turn recovery",
        )
    )

    accumulator = TurnTraceAccumulator(session_id=session_id, org_id=org_id, turn_index=1)
    # Simulate mid-turn death: the running marker landed, no terminal write ever did.
    await begin_turn_trace(db_manager, accumulator)

    traces = TurnTraceStore(db_manager)
    running = await traces.list_for_session(session_id, org_id)
    assert [trace.status for trace in running] == [TURN_STATUS_RUNNING]
    assert running[0].ended_at is None
    assert running[0].id == accumulator.trace_id

    recovered = await recover_interrupted_turns(admin_db_manager)
    assert recovered >= 1

    after = await traces.list_for_session(session_id, org_id)
    assert len(after) == 1
    assert after[0].status == TURN_STATUS_INTERRUPTED
    assert after[0].ended_at is not None
    assert after[0].error == INTERRUPTED_TURN_ERROR

    app.state.db_manager = db_manager
    app.state.session_store = store
    if not any(
        getattr(route, "path", "") == "/v1/sessions/{session_id}/turns" for route in app.routes
    ):
        app.include_router(turns_router)

    headers = {
        "X-Tenant-Org-Id": org_id,
        "X-Tenant-User-Id": user_id,
        "X-User-Role": "developer",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/sessions/{session_id}/turns", headers=headers)

    assert response.status_code == 200
    turn = response.json()["turns"][0]
    assert turn["status"] == TURN_STATUS_INTERRUPTED
    assert turn["error"] == INTERRUPTED_TURN_ERROR
    assert turn["ended_at"] is not None


@pytest.mark.asyncio
async def test_a_replica_recovers_only_turns_whose_owner_stopped_heartbeating(
    db_manager: Any, admin_db_manager: Any
) -> None:
    """Replicas > 1: a starting replica must not interrupt turns its healthy peers run."""
    from api.services.turn_trace import LEASE_TTL_SECONDS, beat_turn_leases

    await _ensure_turn_trace_rls()
    suffix = uuid.uuid4().hex[:6]
    org_id, user_id = f"org_lease_{suffix}", f"user_lease_{suffix}"
    store = PostgresSessionStore(db_manager)
    traces = TurnTraceStore(db_manager)

    async def start_turn(label: str) -> TurnTraceAccumulator:
        session_id = f"sess_{label}_{suffix}"
        await store.create_session(
            SessionRecord(
                session_id=session_id,
                tenant_org_id=org_id,
                tenant_user_id=user_id,
                title=label,
            )
        )
        accumulator = TurnTraceAccumulator(session_id=session_id, org_id=org_id, turn_index=1)
        await begin_turn_trace(db_manager, accumulator)
        return accumulator

    live = await start_turn("live")
    dead = await start_turn("dead")
    never_beat = await start_turn("legacy")

    # `dead` was last heard from long ago; `legacy` predates leases (no heartbeat at all).
    long_ago = int(time.time()) - 10 * LEASE_TTL_SECONDS
    async with admin_db_manager.session() as session:
        await session.execute(
            text(
                "UPDATE turn_traces SET heartbeat_at = :t, owner_instance = 'gone' WHERE id = :id"
            ),
            {"t": long_ago, "id": dead.trace_id},
        )
        await session.execute(
            text(
                "UPDATE turn_traces SET heartbeat_at = NULL, owner_instance = NULL WHERE id = :id"
            ),
            {"id": never_beat.trace_id},
        )
    assert await beat_turn_leases(admin_db_manager) >= 1  # this process proves it is alive

    await recover_interrupted_turns(admin_db_manager, stale_after_seconds=LEASE_TTL_SECONDS)

    async def status_of(accumulator: TurnTraceAccumulator) -> str:
        (trace,) = await traces.list_for_session(accumulator.session_id, org_id)
        return trace.status

    assert await status_of(live) == TURN_STATUS_RUNNING
    assert await status_of(dead) == TURN_STATUS_INTERRUPTED
    assert await status_of(never_beat) == TURN_STATUS_INTERRUPTED

    # A single-replica startup (no TTL) still recovers everything left running.
    await recover_interrupted_turns(admin_db_manager)
    assert await status_of(live) == TURN_STATUS_INTERRUPTED
