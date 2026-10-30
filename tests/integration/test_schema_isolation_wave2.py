"""Tenant-isolation tests for the wave-2 schema (spec sections 8.1.4, 8.2.1, 8.3, 8.4).

Every new table must be invisible across tenant boundaries: Row-Level Security is enabled
*and forced*, the ``tenant_isolation_<table>`` policy keys on ``app.current_org_id``, and a
read issued under one organisation can never observe another organisation's rows.

The application connection is deliberately a non-superuser role: PostgreSQL superusers
bypass the row-security system even with ``FORCE``, so a superuser read would make this
suite pass while production leaked.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from api.db.models import (
    AttachmentModel,
    MemoryModel,
    OrganizationModel,
    SessionGoalModel,
    SessionModel,
    SessionSummaryModel,
    SlackBotModel,
    SlackRouteDecisionModel,
    SlackWorkspaceQuotaModel,
    SubagentTranscriptModel,
    UserModel,
)
from api.db.session import DatabaseSessionManager
from sqlalchemy import select, text

ADMIN_DB_URL = os.getenv("ADMIN_DATABASE_URL", "")
TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")

# Tables added in wave 2 (004 modifies `sessions`, which already carries RLS from 001).
NEW_WAVE2_TABLES: tuple[str, ...] = (
    "attachments",
    "memories",
    "session_summaries",
    "slack_workspace_quotas",
    # Added by M12 (migration 009) and covered by the same isolation guarantees.
    "session_messages",
    "session_compactions",
    "session_goals",
    "subagent_transcripts",
    "slack_bots",
    "slack_route_decisions",
)
RLS_TABLES: tuple[str, ...] = ("sessions", *NEW_WAVE2_TABLES)


def _application_url() -> str:
    """Return a non-superuser URL for the test database.

    The application role (``rocket_app``) is the only one PostgreSQL RLS actually binds:
    a superuser connection bypasses row security by kernel design. We derive the URL from
    ``TEST_DATABASE_URL`` so the same scratch database is used, but force the app role.
    """
    explicit = os.getenv("APP_DATABASE_URL")
    if explicit:
        return explicit
    match = re.match(r"^(?P<scheme>[^:]+://)(?:[^@/]*@)?(?P<rest>.*)$", TEST_DB_URL)
    if not match:
        return TEST_DB_URL
    return f"{match.group('scheme')}rocket_app:rocket_app@{match.group('rest')}"


@pytest.fixture
async def admin_manager(require_database: Callable[[Exception, str], None]) -> AsyncIterator[Any]:
    """Schema-owner connection used to provision RLS and grant the app role."""
    if not ADMIN_DB_URL:
        pytest.skip("ADMIN_DATABASE_URL not configured")
    manager = DatabaseSessionManager(ADMIN_DB_URL)
    try:
        async with manager.session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as err:
        require_database(err, ADMIN_DB_URL)

    yield manager
    await manager.close()


@pytest.fixture
async def app_manager(require_database: Callable[[Exception, str], None]) -> AsyncIterator[Any]:
    """Non-superuser connection whose reads must obey RLS."""
    url = _application_url()
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


async def _grant_application_role(admin: Any) -> None:
    """Create the app role if needed and grant it access to the metadata-created tables.

    CI provisions the schema from SQLAlchemy metadata as the superuser, so the new tables
    are owned by ``postgres`` and the app role needs explicit grants before it can read.
    """
    async with admin.session() as session:
        await session.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rocket_app') THEN
                        CREATE ROLE rocket_app WITH LOGIN PASSWORD 'rocket_app';
                    END IF;
                END
                $$;
                """
            )
        )
        await session.execute(
            text("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO rocket_app;")
        )
        await session.execute(
            text("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO rocket_app;")
        )


async def _ensure_wave2_rls(admin: Any) -> None:
    """Apply the policies the migrations define when the schema came from metadata alone.

    ``Base.metadata.create_all`` never alters an existing table and ``apply_rls_policies``
    only knows the pre-wave-2 tables, so the isolation test provisions these itself.
    """
    async with admin.session() as session:
        for table in NEW_WAVE2_TABLES:
            policy = f"tenant_isolation_{table}"
            await session.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;"))
            await session.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;"))
            exists = await session.scalar(
                text("SELECT 1 FROM pg_policies WHERE tablename = :table AND policyname = :policy"),
                {"table": table, "policy": policy},
            )
            if exists is None:
                await session.execute(
                    text(
                        f"""
                        CREATE POLICY {policy} ON {table}
                            FOR ALL
                            USING (org_id = current_setting('app.current_org_id', true));
                        """
                    )
                )


async def _seed_tenant(admin: Any, org_id: str, user_id: str, session_id: str) -> None:
    """Insert an org, its user and one session so the new tables' foreign keys resolve."""
    async with admin.session() as session:
        session.add(OrganizationModel(id=org_id, name=org_id, slug=f"{org_id.replace('_', '-')}"))
        session.add(
            UserModel(
                id=user_id,
                org_id=org_id,
                email=f"{user_id}@rocket.chat",
                name=user_id,
            )
        )
        await session.flush()
        session.add(SessionModel(id=session_id, org_id=org_id, user_id=user_id, title=session_id))


@pytest.mark.asyncio
async def test_new_wave2_tables_and_sessions_have_forced_rls(admin_manager: Any) -> None:
    """RLS must be enabled *and* forced on every new table, with the expected policy."""
    await _ensure_wave2_rls(admin_manager)

    async with admin_manager.session() as session:
        for table in RLS_TABLES:
            enabled, forced = (
                await session.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname = :table"
                    ),
                    {"table": table},
                )
            ).one()
            assert enabled is True, f"{table} has RLS disabled"
            assert forced is True, f"{table} has RLS enabled but not forced"

            policy = await session.scalar(
                text(
                    "SELECT policyname FROM pg_policies WHERE tablename = :table "
                    "AND policyname = :policy"
                ),
                {"table": table, "policy": f"tenant_isolation_{table}"},
            )
            assert policy == f"tenant_isolation_{table}", f"{table} is missing its policy"


@pytest.mark.asyncio
async def test_new_wave2_tables_deny_cross_tenant_reads(
    admin_manager: Any, app_manager: Any
) -> None:
    """A read under org B must never observe the org-A rows seeded in every new table."""
    await _grant_application_role(admin_manager)
    await _ensure_wave2_rls(admin_manager)

    suffix = uuid.uuid4().hex[:6]
    org_a, org_b = f"org_a_{suffix}", f"org_b_{suffix}"
    user_a, user_b = f"user_a_{suffix}", f"user_b_{suffix}"
    session_a, session_b = f"sess_a_{suffix}", f"sess_b_{suffix}"
    await _seed_tenant(admin_manager, org_a, user_a, session_a)
    await _seed_tenant(admin_manager, org_b, user_b, session_b)

    now = int(time.time())
    embedding = [0.1] * 1536
    sha256 = suffix * 2  # 12 hex chars repeated to length 12; any fixed string works

    async with app_manager.session(tenant_org_id=org_a) as session:
        session.add(
            AttachmentModel(
                id=f"att_{uuid.uuid4().hex[:12]}",
                session_id=session_a,
                org_id=org_a,
                name="server.log",
                mime="text/plain",
                size=42,
                sha256=sha256,
                workspace_path=f"/workspace/.attachments/{sha256}/server.log",
                created_at=now,
            )
        )
        session.add(
            MemoryModel(
                id=f"mem_{uuid.uuid4().hex[:12]}",
                org_id=org_a,
                user_id=user_a,
                scope="org",
                kind="fact",
                content="Production deploys run at 09:00 UTC.",
                embedding=embedding,
                source_session_id=session_a,
                confidence=1.0,
                created_at=now,
            )
        )
        session.add(
            SessionSummaryModel(
                session_id=session_a,
                org_id=org_a,
                summary="Discussed production deploy windows.",
                embedding=embedding,
                turn_start=1,
                turn_end=3,
                updated_at=now,
            )
        )
        session.add(
            SlackWorkspaceQuotaModel(
                team_id=f"team_{suffix}",
                org_id=org_a,
                turns_per_hour=60,
                max_concurrent_sessions=5,
                turns_this_hour=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            SlackBotModel(
                id=f"bot_{suffix}",
                org_id=org_a,
                name="Triage Bot",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            SlackRouteDecisionModel(
                id=f"dec_{suffix}",
                org_id=org_a,
                bot_id=f"bot_{suffix}",
                channel_id="C_MAIN",
                thread_ts="1700000000.1",
                created_at=now,
            )
        )
        session.add(
            SessionGoalModel(
                id=f"goal_{suffix}",
                session_id=session_a,
                org_id=org_a,
                description="Refactor payments to microservice",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            SubagentTranscriptModel(
                id=f"sat_{suffix}",
                session_id=session_a,
                org_id=org_a,
                subagent_id="sub_test_123",
                role="security_auditor",
                task="Audit SQL injection",
                summary="No vulnerabilities found",
                status="completed",
                turns_taken=2,
                tool_calls_count=3,
                duration_ms=1500,
                tokens=450,
                cost_usd=0.015,
                changed_files=[],
                artefacts=[],
                messages=[],
                created_at=now,
            )
        )

    for model in (
        AttachmentModel,
        MemoryModel,
        SessionSummaryModel,
        SlackWorkspaceQuotaModel,
        SlackBotModel,
        SlackRouteDecisionModel,
        SessionGoalModel,
        SubagentTranscriptModel,
    ):
        # The owning tenant sees exactly its row; no explicit WHERE clause is used, so a
        # non-empty result can only come from RLS leaking the row.
        async with app_manager.session(tenant_org_id=org_a) as session:
            visible_to_owner = (await session.scalars(select(model))).all()
        assert len(visible_to_owner) == 1, f"{model.__tablename__} is unreadable by its owner"

        async with app_manager.session(tenant_org_id=org_b) as session:
            visible_to_other = (await session.scalars(select(model))).all()
        assert visible_to_other == [], f"{model.__tablename__} leaked rows across tenants"


@pytest.mark.asyncio
async def test_session_messages_roundtrip_ordering_and_isolation(
    admin_manager: Any, app_manager: Any
) -> None:
    """The durable transcript keeps order and tool context, and never crosses tenants."""
    from api.services.transcript import PostgresTranscriptStore

    from specifications.interfaces.llm import ChatMessage

    await _grant_application_role(admin_manager)
    await _ensure_wave2_rls(admin_manager)
    tag = uuid.uuid4().hex[:8]
    org_a, org_b = f"tx_org_a_{tag}", f"tx_org_b_{tag}"
    sess_a, sess_b = f"tx_sess_a_{tag}", f"tx_sess_b_{tag}"
    await _seed_tenant(admin_manager, org_a, f"tx_user_a_{tag}", sess_a)
    await _seed_tenant(admin_manager, org_b, f"tx_user_b_{tag}", sess_b)

    store = PostgresTranscriptStore(app_manager)
    call = {"id": "c1", "type": "function", "function": {"name": "think", "arguments": "{}"}}
    await store.append(
        sess_a,
        org_a,
        [
            ChatMessage(role="user", content="first"),
            ChatMessage(role="assistant", tool_calls=[call]),
        ],
    )
    await store.append(
        sess_a,
        org_a,
        [
            ChatMessage(role="tool", content="result", tool_call_id="c1"),
            ChatMessage(role="assistant", content="done"),
            ChatMessage(role="user", content="second"),
        ],
    )

    loaded = await store.load(sess_a, org_a)
    assert [m.role for m in loaded] == ["user", "assistant", "tool", "assistant", "user"]
    assert loaded[1].tool_calls == [call]
    assert loaded[2].tool_call_id == "c1"

    # The same session id read under another tenant is invisible (RLS), not merely filtered.
    assert await store.load(sess_a, org_b) == []

    copied = await store.copy_prefix(sess_a, sess_b, org_a, user_turns=1)
    assert copied == 4
    assert [m.role for m in await store.load(sess_b, org_a)] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]

    await store.delete(sess_a, org_a)
    assert await store.load(sess_a, org_a) == []
