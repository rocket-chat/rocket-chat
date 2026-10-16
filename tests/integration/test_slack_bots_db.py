"""Slack bots on PostgreSQL: tokens stored sealed, rows invisible to other organizations, and the
bot manager's cross-tenant listing on the schema-owner connection."""

from __future__ import annotations

import os

import pytest
from api.db.session import DatabaseSessionManager, current_tenant_org_id
from api.main import app
from api.services.slack_bots import SlackBotRecord, SlackBotStore, SlackTokens
from fastapi.testclient import TestClient
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    not (os.getenv("TEST_DATABASE_URL") and os.getenv("ADMIN_DATABASE_URL")),
    reason="needs PostgreSQL and the schema-owner connection",
)

BOT_TOKEN = "xoxb-1111-2222-sealedsecretvalue"
APP_TOKEN = "xapp-1-A1-3333-sealedsecretvalue"


def test_bots_are_sealed_isolated_and_listed_for_the_manager() -> None:
    with TestClient(app) as client:
        manager = app.state.db_manager
        owner = DatabaseSessionManager(os.environ["ADMIN_DATABASE_URL"])

        async def scenario() -> tuple[object, ...]:
            current_tenant_org_id.set("test_org")
            store = SlackBotStore(manager)
            created = await store.create(
                SlackBotRecord(id="", org_id="test_org", name="Ops"),
                SlackTokens(BOT_TOKEN, APP_TOKEN),
            )
            try:
                mine = [b.id for b in await store.list("test_org")]
                theirs = [b.id for b in await store.list("usage_org")]
                async with owner.session() as session:
                    stored = (
                        await session.execute(
                            text(
                                "SELECT bot_token_encrypted, app_token_encrypted "
                                "FROM slack_bots WHERE id = :id"
                            ),
                            {"id": created.id},
                        )
                    ).one()
                listed = {r.id: t for r, t in await store.enabled_bots(owner)}
                current_tenant_org_id.set("usage_org")
                unscoped = await store.enabled_bots(manager)
                return created, mine, theirs, tuple(stored), listed, unscoped
            finally:
                await store.delete("test_org", created.id)
                await owner.close()

        created, mine, theirs, stored, listed, unscoped = client.portal.call(scenario)

    assert created.bot_token_fingerprint == "xox...alue"  # type: ignore[attr-defined]
    assert created.id in mine  # type: ignore[attr-defined, operator]
    assert theirs == []
    assert all(BOT_TOKEN not in str(v) and APP_TOKEN not in str(v) for v in stored)
    assert listed[created.id] == SlackTokens(BOT_TOKEN, APP_TOKEN)  # type: ignore[attr-defined, index]
    assert created.id not in [r.id for r, _ in unscoped], (  # type: ignore[attr-defined, union-attr]
        "on the application connection, another organization's bots stay hidden"
    )
