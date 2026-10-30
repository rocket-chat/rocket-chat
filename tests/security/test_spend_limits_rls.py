"""Spend limits are each organization's own, under forced Row-Level Security."""

from __future__ import annotations

from api.db.models import SpendLimitModel
from api.db.session import DatabaseSessionManager
from api.services.spend_limits import SpendLimitStore

from tests.support.rls import (
    TEST_DATABASE_URL,
    add_row,
    assert_cannot_write_into,
    assert_isolated,
    needs_postgres,
    spend_limit_row,
    two_orgs,
)


@needs_postgres
async def test_spend_limits_are_isolated_in_postgres() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org_a, org_b = await two_orgs(db)
        limit = spend_limit_row(org_a)
        await add_row(db, org_a, limit)
        store = SpendLimitStore(db)

        await assert_isolated(db, SpendLimitModel, org_a, org_b)
        assert await store.list(org_b) == []
        assert await store.update(org_b, limit.id, amount_usd=1000.0) is None
        assert await store.delete(org_b, limit.id) is None
        kept = await store.get(org_a, limit.id)
        assert kept is not None and kept.amount_usd == 5.0
        await assert_cannot_write_into(db, org_b, spend_limit_row(org_a))
    finally:
        await db.close()
