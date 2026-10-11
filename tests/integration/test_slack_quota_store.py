"""Integration test for the PostgreSQL-backed Slack workspace quota store.

Exercises the real read-modify-write path against the scratch database: the hourly
counter is consumed, the next request is denied with a retry hint, and after the window
rolls the workspace is admitted again. A workspace with no row configured is uncapped.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable

import pytest
from api.db.models import OrganizationModel, SlackWorkspaceQuotaModel
from api.db.session import DatabaseSessionManager
from api.services.quota import SlackWorkspaceQuotaService
from sqlalchemy import text

ADMIN_DB_URL = os.getenv("ADMIN_DATABASE_URL", "")


@pytest.mark.asyncio
async def test_postgres_quota_store_consumes_denies_and_expires(
    require_database: Callable[[Exception, str], None],
) -> None:
    if not ADMIN_DB_URL:
        pytest.skip("ADMIN_DATABASE_URL not configured")

    manager = DatabaseSessionManager(ADMIN_DB_URL)
    try:
        try:
            async with manager.session() as session:
                await session.execute(text("SELECT 1"))
        except Exception as err:
            require_database(err, ADMIN_DB_URL)
            return

        org_id = f"s7_org_{uuid.uuid4().hex[:8]}"
        team_id = f"T_{uuid.uuid4().hex[:8]}"
        now = 1_000_000.0

        async with manager.session() as session:
            session.add(OrganizationModel(id=org_id, name=org_id, slug=org_id.replace("_", "-")))
            await session.flush()
            session.add(
                SlackWorkspaceQuotaModel(
                    team_id=team_id,
                    org_id=org_id,
                    monthly_usd_cap=0.0,
                    turns_per_hour=1,
                    max_concurrent_sessions=0,
                    current_month_spend_usd=0.0,
                    turns_this_hour=0,
                    hour_window_start=None,
                    created_at=int(now),
                    updated_at=int(now),
                )
            )

        service = SlackWorkspaceQuotaService(db_manager=manager, clock=lambda: now)

        first = await service.evaluate(
            team_id=team_id,
            org_id=org_id,
            user_id="U1",
            channel_id="C1",
            active_sessions=0,
            now=now,
        )
        assert first.allowed is True

        second = await service.evaluate(
            team_id=team_id,
            org_id=org_id,
            user_id="U1",
            channel_id="C1",
            active_sessions=0,
            now=now,
        )
        assert second.allowed is False
        assert second.reason == "turns_per_hour"

        # The counter must have been persisted, not kept in process memory.
        async with manager.session(tenant_org_id=org_id) as session:
            stored = await session.get(SlackWorkspaceQuotaModel, team_id)
            assert stored is not None
            assert stored.turns_this_hour == 1

        after_window = await service.evaluate(
            team_id=team_id,
            org_id=org_id,
            user_id="U1",
            channel_id="C1",
            active_sessions=0,
            now=now + 3601,
        )
        assert after_window.allowed is True

        # An unconfigured workspace has no cap row and is admitted.
        uncapped = await service.evaluate(
            team_id=f"T_missing_{uuid.uuid4().hex[:6]}",
            org_id=org_id,
            user_id="U1",
            channel_id="C1",
            active_sessions=0,
            now=now,
        )
        assert uncapped.allowed is True
    finally:
        try:
            async with manager.session() as session:
                await session.execute(
                    text("DELETE FROM slack_workspace_quotas WHERE org_id = :org"),
                    {"org": org_id},
                )
                await session.execute(
                    text("DELETE FROM organizations WHERE id = :org"), {"org": org_id}
                )
        except Exception:
            pass
        await manager.close()
