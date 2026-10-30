"""Alembic is the only schema path and it builds exactly what the models describe."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from api.db.models import Base
from api.db.schema import (
    SchemaNotMigratedError,
    assert_schema_current,
    current_revision,
    head_revision,
)
from api.db.session import DatabaseSessionManager
from sqlalchemy import text
from sqlalchemy.engine import make_url

ADMIN_URL = os.getenv("ADMIN_DATABASE_URL", "")
REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="ADMIN_DATABASE_URL not configured")

# Tables without a tenant policy by design: the tenant registry itself and the pre-tenant lookups.
TABLES_WITHOUT_RLS = {
    "organizations",
    "users",
    "slack_workspaces",
    "github_installations",
}


@pytest.fixture
async def migrated_db() -> AsyncIterator[DatabaseSessionManager]:
    """A scratch database built only by ``alembic upgrade head``."""
    name = f"rocket_mig_{uuid.uuid4().hex[:8]}"
    base = make_url(ADMIN_URL)
    server = DatabaseSessionManager(base.set(database="postgres").render_as_string(False))
    assert server.engine is not None
    async with server.engine.connect() as conn:
        await (await conn.execution_options(isolation_level="AUTOCOMMIT")).execute(
            text(f'CREATE DATABASE "{name}"')
        )
    scratch_url = base.set(database=name).render_as_string(False)
    await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": scratch_url},
        check=True,
        capture_output=True,
    )
    manager = DatabaseSessionManager(scratch_url)
    yield manager
    await manager.close()
    assert server.engine is not None
    async with server.engine.connect() as conn:
        await (await conn.execution_options(isolation_level="AUTOCOMMIT")).execute(
            text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        )
    await server.close()


@pytest.mark.asyncio
async def test_alembic_alone_creates_every_model_table_and_column(
    migrated_db: DatabaseSessionManager,
) -> None:
    async with migrated_db.session() as session:
        rows = await session.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
        )
        actual: dict[str, set[str]] = {}
        for table, column in rows:
            actual.setdefault(table, set()).add(column)

    for table in Base.metadata.sorted_tables:
        assert table.name in actual, f"migrations never create '{table.name}'"
        missing = {c.name for c in table.columns} - actual[table.name]
        assert not missing, f"'{table.name}' lacks columns {sorted(missing)} in the migrations"


@pytest.mark.asyncio
async def test_alembic_alone_forces_row_level_security_on_every_tenant_table(
    migrated_db: DatabaseSessionManager,
) -> None:
    async with migrated_db.session() as session:
        rows = await session.execute(
            text(
                "SELECT c.relname, c.relrowsecurity AND c.relforcerowsecurity, "
                "EXISTS (SELECT 1 FROM information_schema.columns k WHERE k.table_name = c.relname "
                "AND k.column_name = 'org_id' AND k.table_schema = 'public') "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'"
            )
        )
        tenant_tables = {name: forced for name, forced, has_org in rows if has_org}

    unprotected = {
        name
        for name, forced in tenant_tables.items()
        if not forced and name not in TABLES_WITHOUT_RLS
    }
    assert not unprotected, f"tenant tables without forced RLS after migrations: {unprotected}"


@pytest.mark.asyncio
async def test_the_startup_check_accepts_head_and_rejects_anything_else(
    migrated_db: DatabaseSessionManager,
) -> None:
    assert await current_revision(migrated_db) == head_revision()
    await assert_schema_current(migrated_db)

    async with migrated_db.session() as session:
        await session.execute(text("UPDATE alembic_version SET version_num = '001_old'"))
    with pytest.raises(SchemaNotMigratedError, match="alembic upgrade head"):
        await assert_schema_current(migrated_db)


@pytest.mark.asyncio
async def test_an_unmigrated_database_is_reported_as_such() -> None:
    name = f"rocket_empty_{uuid.uuid4().hex[:8]}"
    base = make_url(ADMIN_URL)
    server = DatabaseSessionManager(base.set(database="postgres").render_as_string(False))
    assert server.engine is not None
    async with server.engine.connect() as conn:
        await (await conn.execution_options(isolation_level="AUTOCOMMIT")).execute(
            text(f'CREATE DATABASE "{name}"')
        )
    empty = DatabaseSessionManager(base.set(database=name).render_as_string(False))
    try:
        assert await current_revision(empty) is None
        with pytest.raises(SchemaNotMigratedError):
            await assert_schema_current(empty)
    finally:
        await empty.close()
        async with server.engine.connect() as conn:
            await (await conn.execution_options(isolation_level="AUTOCOMMIT")).execute(
                text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            )
        await server.close()
