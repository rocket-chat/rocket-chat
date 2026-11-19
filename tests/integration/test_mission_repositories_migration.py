"""Migration 071 moves a mission's repository into its list and keeps existing missions'
GitHub writes; the downgrade puts back the first repository."""

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

_BEFORE = "070_change_proposals"
_AFTER = "071_mission_repos_writes"


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
async def database_at_070() -> AsyncIterator[tuple[str, DatabaseSessionManager]]:
    name = f"rocket_repos_{uuid.uuid4().hex[:8]}"
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


def _mission(mission_id: str, repository: str) -> str:
    return (
        "INSERT INTO missions (id, org_id, scope, user_id, name, instructions, triggers, "
        "deliveries, secrets, git_repo, created_at, updated_at) VALUES "
        f"('{mission_id}', 'o1', 'user', 'u1', 'M', '', '[]', '[]', '{{}}', '{repository}', 1, 1)"
    )


@pytest.mark.asyncio
async def test_a_missions_repository_becomes_its_list_and_its_writes_stay_on(
    database_at_070: tuple[str, DatabaseSessionManager],
) -> None:
    url, manager = database_at_070
    async with manager.session() as session:
        for statement in (
            "INSERT INTO organizations (id, name, slug, created_at, settings) "
            "VALUES ('o1', 'O', 'o', 0, '{}')",
            "INSERT INTO users (id, org_id, email, name, created_at, settings) "
            "VALUES ('u1', 'o1', 'u@o.test', 'U', 0, '{}')",
            _mission("mission_url", "https://github.com/acme/api.git"),
            _mission("mission_none", ""),
        ):
            await session.execute(text(statement))

    await _alembic(url, "upgrade", _AFTER)
    rows = await _rows(manager, "SELECT id, git_repos, github_write FROM missions ORDER BY id")
    assert rows == [
        {"id": "mission_none", "git_repos": [], "github_write": True},
        {"id": "mission_url", "git_repos": ["acme/api"], "github_write": True},
    ]

    await _alembic(url, "downgrade", _BEFORE)
    rows = await _rows(manager, "SELECT id, git_repo FROM missions ORDER BY id")
    assert rows == [
        {"id": "mission_none", "git_repo": ""},
        {"id": "mission_url", "git_repo": "acme/api"},
    ]
