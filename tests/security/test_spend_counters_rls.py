"""Spend counters are each organization's own, under forced Row-Level Security."""

from __future__ import annotations

from api.db.models import SpendCounterModel
from api.db.session import DatabaseSessionManager

from tests.support.rls import (
    TEST_DATABASE_URL,
    add_row,
    assert_cannot_write_into,
    assert_isolated,
    needs_postgres,
    spend_limit_row,
    two_orgs,
)


def _counter(org_id: str, limit_id: str, window_start: int) -> SpendCounterModel:
    return SpendCounterModel(
        limit_id=limit_id,
        subject_key="org",
        window_start=window_start,
        org_id=org_id,
        spent_usd=4.0,
        reserved_usd=0.5,
        updated_at=window_start,
    )


@needs_postgres
async def test_spend_counters_are_isolated_in_postgres() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org_a, org_b = await two_orgs(db)
        limit = spend_limit_row(org_a)
        await add_row(db, org_a, limit)
        await add_row(db, org_a, _counter(org_a, limit.id, 0))

        await assert_isolated(db, SpendCounterModel, org_a, org_b)
        await assert_cannot_write_into(db, org_b, _counter(org_a, limit.id, 3600))
    finally:
        await db.close()
