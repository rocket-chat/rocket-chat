"""The background retention sweep deletes what an organization's governance windows expire."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from api.bootstrap.background import retention_policy_for
from api.db.models import OrganizationModel
from api.db.session import DatabaseSessionManager
from api.services.audit import AuditService, RetentionSweeper
from api.services.settings import save_org_config
from sqlalchemy import text

DAY = 86400


@pytest.fixture
async def managers(
    require_database: Callable[[Exception, str], None],
) -> AsyncIterator[tuple[DatabaseSessionManager, DatabaseSessionManager]]:
    app_url, admin_url = os.getenv("TEST_DATABASE_URL"), os.getenv("ADMIN_DATABASE_URL")
    if not app_url or not admin_url:
        pytest.skip("TEST_DATABASE_URL and ADMIN_DATABASE_URL are required")
    app, admin = DatabaseSessionManager(app_url), DatabaseSessionManager(admin_url)
    try:
        async with admin.session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as err:
        require_database(err, admin_url)
    yield app, admin
    await app.close()
    await admin.close()


@pytest.mark.setting("governance.audit_retention_days")
async def test_the_sweep_deletes_expired_rows_for_organizations_that_set_a_window(
    managers: tuple[DatabaseSessionManager, DatabaseSessionManager],
) -> None:
    app, admin = managers
    now = int(time.time())
    kept_forever, windowed = f"keep_{uuid.uuid4().hex[:8]}", f"win_{uuid.uuid4().hex[:8]}"
    async with admin.session() as session:
        for org in (kept_forever, windowed):
            session.add(OrganizationModel(id=org, name=org, slug=org))
    try:
        await save_org_config(app, windowed, "governance", {"audit_retention_days": 30}, [])
        for org in (kept_forever, windowed):
            audit = AuditService(app)
            await audit.record("tool_call", org_id=org, tool_name="old", now=now - 90 * DAY)
            await audit.record("tool_call", org_id=org, tool_name="recent", now=now - DAY)

        sweeper = RetentionSweeper(
            admin, policy_provider=lambda org_id: retention_policy_for(admin, org_id)
        )
        counts = await sweeper.sweep(now=now)

        async def remaining(org: str) -> list[Any]:
            async with admin.session() as session:
                rows = await session.execute(
                    text("SELECT tool_name FROM audit_events WHERE org_id = :o ORDER BY tool_name"),
                    {"o": org},
                )
                return [row[0] for row in rows]

        assert counts.get("audit_events", 0) >= 1
        assert await remaining(windowed) == ["recent"]
        assert await remaining(kept_forever) == ["old", "recent"], "no window means keep forever"
    finally:
        async with admin.session() as session:
            for org in (kept_forever, windowed):
                for table in ("audit_events", "org_settings"):
                    await session.execute(
                        text(f"DELETE FROM {table} WHERE org_id = :o"), {"o": org}
                    )
                await session.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org})
