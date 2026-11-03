"""Migration 064 carries every scheduled mission and trigger over to ``missions``, and the
downgrade puts back what the old tables can hold."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from api.db.session import DatabaseSessionManager
from sqlalchemy import text
from sqlalchemy.engine import make_url

ADMIN_URL = os.getenv("ADMIN_DATABASE_URL", "")
REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="ADMIN_DATABASE_URL not configured")

_BEFORE = "063_team_settings_and_sandboxes"
_AFTER = "064_unified_missions"


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
async def database_at_063() -> AsyncIterator[tuple[str, DatabaseSessionManager]]:
    name = f"rocket_missions_{uuid.uuid4().hex[:8]}"
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


async def _rows(manager: DatabaseSessionManager, query: str) -> list[dict[str, Any]]:
    async with manager.session() as session:
        return [dict(row) for row in (await session.execute(text(query))).mappings()]


@pytest.mark.asyncio
async def test_scheduled_missions_and_triggers_become_missions_and_come_back(
    database_at_063: tuple[str, DatabaseSessionManager],
) -> None:
    url, manager = database_at_063
    async with manager.session() as session:
        for statement in (
            "INSERT INTO organizations (id, name, slug, created_at, settings) "
            "VALUES ('o1', 'O', 'o', 0, '{}')",
            "INSERT INTO users (id, org_id, email, name, created_at, settings) "
            "VALUES ('u1', 'o1', 'u@o.test', 'U', 0, '{}')",
            "INSERT INTO scheduled_missions (id, org_id, user_id, name, prompt, cron, timezone, "
            "slack_channel, enabled, created_at) VALUES ('mission_old', 'o1', 'u1', 'Audit', "
            "'Audit deps', '0 9 * * 1', 'Europe/Paris', 'C0123', true, 1)",
            "INSERT INTO triggers (id, org_id, name, source, filter, mode, agent_id, enabled, "
            "created_by, created_at, updated_at) VALUES ('trg_review', 'o1', 'Review', "
            "'github_pr', '{\"repositories\": [\"acme/*\"]}', 'review', 'persona-reviewer', true, "
            "'u1', 2, 2)",
        ):
            await session.execute(text(statement))

    await _alembic(url, "upgrade", _AFTER)
    missions = {m["id"]: m for m in await _rows(manager, "SELECT * FROM missions")}
    old = missions["mission_old"]
    assert (old["scope"], old["user_id"], old["instructions"]) == ("user", "u1", "Audit deps")
    assert old["triggers"] == [
        {"source": "schedule", "schedule": "0 9 * * 1", "timezone": "Europe/Paris"}
    ]
    assert {"kind": "chat", "bot_id": "", "channel": "C0123"} in old["deliveries"]
    review = missions["trg_review"]
    assert (review["scope"], review["mode"], review["agent_id"]) == (
        "org",
        "review",
        "persona-reviewer",
    )
    assert review["triggers"][0]["filter"] == {"repositories": ["acme/*"]}
    assert {"kind": "github_review"} in review["deliveries"]

    await _alembic(url, "downgrade", _BEFORE)
    scheduled = await _rows(
        manager, "SELECT id, prompt, cron, timezone, slack_channel FROM scheduled_missions"
    )
    assert scheduled == [
        {"id": "mission_old", "prompt": "Audit deps", "cron": "0 9 * * 1",
         "timezone": "Europe/Paris", "slack_channel": "C0123"}
    ]  # fmt: skip
    triggers = await _rows(manager, "SELECT id, source, mode FROM triggers")
    assert triggers == [{"id": "trg_review", "source": "github_pr", "mode": "review"}]
