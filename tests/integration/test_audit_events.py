"""Integration tests for the governance audit trail.

Proves the three contracts SG11 depends on: a tool execution leaves an audit row with
actor attribution, an approval decision leaves an approver attribution, and redaction is
applied to the tool output stored in the turn trace.
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
from api.db.models import OrgSettingModel
from api.db.session import DatabaseSessionManager, current_tenant_org_id
from api.db.store import PostgresSessionStore, TurnTraceStore
from api.events import AsyncIOEventBus
from api.main import app
from api.routers.admin_audit import router as admin_audit_router
from api.services.audit import AuditService
from api.turns.runner import execute_turn_background
from httpx import ASGITransport, AsyncClient
from sandbox_driver.fake_driver import FakeSandboxDriver
from sqlalchemy import text

from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk
from specifications.interfaces.session import SessionRecord

ADMIN_DB_URL = os.getenv("ADMIN_DATABASE_URL", "")

HEADERS = {
    "X-Tenant-Org-Id": "default_org",
    "X-Tenant-User-Id": "dev_user",
    "X-User-Role": "admin",
}

SECRET = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"

_RLS_STATEMENTS = (
    "ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;",
    """
    CREATE POLICY tenant_isolation_audit_events ON audit_events
        FOR ALL
        USING (org_id = current_setting('app.current_org_id', true));
    """,
)


@pytest.fixture
async def db_manager(require_database: Callable[[Exception, str], None]) -> AsyncIterator[Any]:
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


async def _ensure_audit_rls() -> None:
    if not ADMIN_DB_URL:
        return
    manager = DatabaseSessionManager(ADMIN_DB_URL)
    try:
        async with manager.session() as session:
            existing = (
                await session.execute(
                    text(
                        "SELECT 1 FROM pg_policies WHERE tablename = 'audit_events' "
                        "AND policyname = 'tenant_isolation_audit_events'"
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


async def _seed_governance(db_manager: Any, org_id: str, config: dict[str, Any]) -> None:
    async with db_manager.session(tenant_org_id=org_id) as session:
        session.add(
            OrgSettingModel(
                id=f"orgset_gov_{uuid.uuid4().hex[:8]}",
                org_id=org_id,
                domain="governance",
                config=config,
                locked_keys=[],
                created_at=int(time.time()),
                updated_at=int(time.time()),
            )
        )


@pytest.mark.asyncio
async def test_tool_call_leaves_audit_row_and_trace_is_redacted(db_manager: Any) -> None:
    """A turn's tool call produces actor-attributed audit rows; the trace carries redaction."""
    session_id = f"sess_gov_{uuid.uuid4().hex[:8]}"
    store = PostgresSessionStore(db_manager)
    await store.create_session(
        SessionRecord(
            session_id=session_id,
            tenant_org_id="default_org",
            tenant_user_id="dev_user",
            title="Governance audit",
        )
    )
    # Auto-approve the critical gate so the turn completes without a human, which also
    # exercises the server-side approval policy.
    await _seed_governance(
        db_manager, "default_org", {"approval_policy": {"auto_approve": ["critical"]}}
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
            {"name": "bash_exec", "arguments": {"command": "echo token"}},
        ]
    )
    driver = FakeSandboxDriver()
    driver.script_command("echo token", _ok(f"leaked {SECRET}\n"))
    registry = ToolRegistry(driver=driver, session_id=session_id)
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)
    bus = AsyncIOEventBus()

    token = current_tenant_org_id.set("default_org")
    try:
        await asyncio.wait_for(
            execute_turn_background(
                session_id=session_id,
                prompt="Revert the workspace then leak a token",
                orchestrator=orchestrator,
                event_bus=bus,
                driver=driver,
                registry=registry,
                store=store,
                db_mgr=db_manager,
            ),
            timeout=30,
        )
    finally:
        current_tenant_org_id.reset(token)

    # The stored trace holds the redacted result, never the secret.
    traces = await TurnTraceStore(db_manager).list_for_session(session_id, "default_org")
    assert len(traces) == 1
    bash_call = next(call for call in traces[0].tool_calls if call["name"] == "bash_exec")
    assert SECRET not in bash_call["result"]
    assert "[REDACTED:openai_key]" in bash_call["result"]
    assert bash_call["redacted"] == 1

    # The audit trail carries actor attribution on the tool call and approver
    # attribution on the policy decision.
    events = await AuditService(db_manager).list_page("default_org", limit=100)
    tool_rows = [e for e in events if e.event_type == "tool_call" and e.tool_name == "bash_exec"]
    assert tool_rows, "a tool call must produce an audit row"
    assert tool_rows[0].actor_user_id == "dev_user"
    assert tool_rows[0].redacted is True
    assert tool_rows[0].redaction_count == 1

    decision_rows = [e for e in events if e.event_type == "approval_decision"]
    assert decision_rows, "an approval decision must produce an audit row"
    assert decision_rows[0].approved_by == "policy:auto_approve"
    assert decision_rows[0].actor_user_id == "dev_user"


def _ok(stdout: str) -> Any:
    from specifications.interfaces.sandbox import ExecResult

    return ExecResult(0, stdout, "", 5)


@pytest.mark.asyncio
async def test_audit_events_are_isolated_between_organizations(db_manager: Any) -> None:
    await _ensure_audit_rls()

    suffix = uuid.uuid4().hex[:6]
    org_a, org_b = f"org_audit_a_{suffix}", f"org_audit_b_{suffix}"

    from api.db.models import OrganizationModel

    async with db_manager.session() as session:
        session.add(OrganizationModel(id=org_a, name=org_a, slug=org_a.replace("_", "-")))
        session.add(OrganizationModel(id=org_b, name=org_b, slug=org_b.replace("_", "-")))

    service = AuditService(db_manager)
    await service.record("tool_call", org_id=org_a, actor_user_id="user_a", tool_name="bash_exec")
    await service.record("tool_call", org_id=org_b, actor_user_id="user_b", tool_name="bash_exec")

    assert len(await service.list_page(org_a, limit=50)) == 1
    assert len(await service.list_page(org_b, limit=50)) == 1
    assert all(event.org_id == org_a for event in await service.list_page(org_a, limit=50))


@pytest.mark.asyncio
async def test_admin_audit_endpoint_paginates_with_cursor(db_manager: Any) -> None:
    await _ensure_audit_rls()

    service = AuditService(db_manager)
    for index in range(3):
        await service.record(
            "tool_call",
            org_id="default_org",
            actor_user_id="dev_user",
            tool_name="bash_exec",
            now=1_700_000_000 + index,
        )

    app.state.db_manager = db_manager
    if not any(getattr(route, "path", "") == "/v1/admin/audit" for route in app.routes):
        app.include_router(admin_audit_router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/v1/admin/audit?limit=2", headers=HEADERS)
        assert first.status_code == 200
        body = first.json()
        assert len(body["events"]) == 2
        assert body["next_cursor"]

        second = await client.get(
            f"/v1/admin/audit?limit=2&cursor={body['next_cursor']}", headers=HEADERS
        )
        assert second.status_code == 200
        assert len(second.json()["events"]) == 1

        non_admin = await client.get(
            "/v1/admin/audit",
            headers={
                "X-Tenant-Org-Id": "default_org",
                "X-Tenant-User-Id": "regular_user",
                "X-User-Role": "developer",
            },
        )
        assert non_admin.status_code == 403
