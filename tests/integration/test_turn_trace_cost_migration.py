"""Migration 046 converts the turn cost already stored in micro-dollars to dollars and back."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from api.db.session import DatabaseSessionManager
from sqlalchemy import text
from sqlalchemy.engine import make_url

ADMIN_URL = os.getenv("ADMIN_DATABASE_URL", "")
REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="ADMIN_DATABASE_URL not configured")

_BEFORE = "045_artifact_versions"
_AFTER = "046_turn_trace_cost_in_dollars"


async def _alembic(url: str, *args: str) -> None:
    await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
    )


@pytest.fixture
async def database_at_045() -> AsyncIterator[tuple[str, DatabaseSessionManager]]:
    name = f"rocket_cost_{uuid.uuid4().hex[:8]}"
    base = make_url(ADMIN_URL)
    server = DatabaseSessionManager(base.set(database="postgres").render_as_string(False))
    assert server.engine is not None
    async with server.engine.connect() as conn:
        await (await conn.execution_options(isolation_level="AUTOCOMMIT")).execute(
            text(f'CREATE DATABASE "{name}"')
        )
    url = base.set(database=name).render_as_string(False)
    await _alembic(url, "upgrade", _BEFORE)
    manager = DatabaseSessionManager(url)
    yield url, manager
    await manager.close()
    async with server.engine.connect() as conn:
        await (await conn.execution_options(isolation_level="AUTOCOMMIT")).execute(
            text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        )
    await server.close()


async def _insert_trace(manager: DatabaseSessionManager, trace_id: str, tokens: object) -> None:
    async with manager.session() as session:
        await session.execute(
            text(
                "INSERT INTO turn_traces (id, session_id, org_id, turn_index, started_at, status, "
                "tool_calls, diff_paths, approvals, tokens) VALUES (:id, 's1', 'o1', 1, 0, "
                "'completed', '[]', '[]', '[]', CAST(:tokens AS jsonb))"
            ),
            {"id": trace_id, "tokens": json.dumps(tokens)},
        )


async def _costs(manager: DatabaseSessionManager) -> dict[str, object]:
    async with manager.session() as session:
        rows = await session.execute(
            text("SELECT id, tokens #> '{usage,cost_usd}' FROM turn_traces ORDER BY id")
        )
        return {row[0]: row[1] for row in rows}


@pytest.mark.asyncio
async def test_stored_turn_costs_become_dollars_and_the_downgrade_restores_them(
    database_at_045: tuple[str, DatabaseSessionManager],
) -> None:
    url, manager = database_at_045
    async with manager.session() as session:
        for statement in (
            "INSERT INTO organizations (id, name, slug, created_at, settings) "
            "VALUES ('o1', 'O', 'o', 0, '{}')",
            "INSERT INTO users (id, org_id, email, name, created_at, settings) "
            "VALUES ('u1', 'o1', 'u@o.test', 'U', 0, '{}')",
            "INSERT INTO sessions (id, org_id, user_id, title, conversation_history, "
            "metadata_json, created_at, updated_at) VALUES ('s1', 'o1', 'u1', 'T', '[]', '{}', 0, 0)",
        ):
            await session.execute(text(statement))
    await _insert_trace(manager, "t1", {"usage": {"prompt_tokens": 10, "cost_usd": 4500}})
    await _insert_trace(manager, "t2", {"effective_model": "m"})

    await _alembic(url, "upgrade", _AFTER)
    assert await _costs(manager) == {"t1": 0.0045, "t2": None}

    await _alembic(url, "downgrade", _BEFORE)
    assert await _costs(manager) == {"t1": 4500, "t2": None}
