"""The steps every forced row-level-security test repeats: two organizations, a row in one of
them, and proof that the other neither reads nor writes it."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from api.db.models import Base, OrganizationModel, SpendLimitModel, UserModel
from api.db.session import DatabaseSessionManager
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

needs_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="Requires disposable test PostgreSQL with RLS"
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def two_orgs(db: DatabaseSessionManager) -> tuple[str, str]:
    """Two organizations, each with a user ``u_<org>``."""
    orgs = (new_id("org"), new_id("org"))
    now = int(time.time())
    for org in orgs:
        async with db.session(tenant_org_id=org) as s:
            s.add(OrganizationModel(id=org, name=org, slug=org, created_at=now))
            s.add(
                UserModel(id=f"u_{org}", org_id=org, email=f"{org}@t.io", name="U", created_at=now)
            )
    return orgs


def spend_limit_row(org_id: str) -> SpendLimitModel:
    """An organization-wide daily limit of 5 USD (the parent of counters)."""
    return SpendLimitModel(
        id=new_id("lim"),
        org_id=org_id,
        subject_kind="org",
        period="day",
        amount_usd=5.0,
        mode="block",
        created_by=f"u_{org_id}",
    )


async def add_row(db: DatabaseSessionManager, org_id: str, row: Base) -> None:
    async with db.session(tenant_org_id=org_id) as s:
        s.add(row)


async def assert_isolated(
    db: DatabaseSessionManager, model: type[Base], owner: str, other: str
) -> None:
    """``owner`` sees its rows of ``model``; ``other`` sees none of them, even unfiltered."""
    org_column = model.__table__.c.org_id
    async with db.session(tenant_org_id=other) as s:
        leaked = (await s.execute(select(org_column).where(org_column == owner))).all()
        unfiltered = {org for (org,) in (await s.execute(select(org_column))).all()}
    assert leaked == [], f"{model.__tablename__}: another organization read the rows"
    assert owner not in unfiltered
    async with db.session(tenant_org_id=owner) as s:
        assert (await s.execute(select(org_column).where(org_column == owner))).all()


async def assert_cannot_write_into(db: DatabaseSessionManager, acting: str, row: Base) -> None:
    """A session of ``acting`` cannot insert a row naming another organization."""
    with pytest.raises(DBAPIError):
        await add_row(db, acting, row)
