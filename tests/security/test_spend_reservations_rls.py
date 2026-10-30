"""Spend reservations are each organization's own, under forced Row-Level Security."""

from __future__ import annotations

import time

from api.db.models import SpendReservationModel
from api.db.session import DatabaseSessionManager

from tests.support.rls import (
    TEST_DATABASE_URL,
    add_row,
    assert_cannot_write_into,
    assert_isolated,
    needs_postgres,
    new_id,
    two_orgs,
)


def _reservation(org_id: str) -> SpendReservationModel:
    return SpendReservationModel(
        id=new_id("res"),
        org_id=org_id,
        amount_usd=0.25,
        counters=[["lim_x", "org", 0]],
        created_at=int(time.time()),
    )


@needs_postgres
async def test_spend_reservations_are_isolated_in_postgres() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    try:
        org_a, org_b = await two_orgs(db)
        await add_row(db, org_a, _reservation(org_a))

        await assert_isolated(db, SpendReservationModel, org_a, org_b)
        await assert_cannot_write_into(db, org_b, _reservation(org_a))
    finally:
        await db.close()
