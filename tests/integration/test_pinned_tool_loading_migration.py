"""Migration 048 keeps what agents with pinned tools load: they move to the ``pinned`` mode.

"On demand" used to load only an agent's pinned tools once it had any; it now loads the
platform's own tools too, and the old behaviour is the new ``pinned`` mode.
"""

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

_BEFORE = "047_api_tokens"
_AFTER = "048_pinned_tool_loading"


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
async def database_at_047() -> AsyncIterator[tuple[str, DatabaseSessionManager]]:
    name = f"rocket_pinned_{uuid.uuid4().hex[:8]}"
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


async def _organization(
    manager: DatabaseSessionManager, org: str, tool_loading: str | None
) -> None:
    config = {} if tool_loading is None else {"tool_loading": tool_loading}
    async with manager.session() as session:
        await session.execute(
            text(
                "INSERT INTO organizations (id, name, slug, created_at, settings) "
                "VALUES (:org, :org, :org, 0, '{}')"
            ),
            {"org": org},
        )
        await session.execute(
            text(
                "INSERT INTO org_settings (id, org_id, domain, config, locked_keys, created_at, "
                "updated_at) VALUES (:id, :org, 'general', CAST(:config AS jsonb), '[]', 0, 0)"
            ),
            {"id": f"os-{org}", "org": org, "config": json.dumps(config)},
        )


async def _persona(
    manager: DatabaseSessionManager, org: str, name: str, mode: str | None, pins: list[str]
) -> None:
    async with manager.session() as session:
        await session.execute(
            text(
                "INSERT INTO agent_personas (id, org_id, name, role_title, system_prompt, model, "
                "whitelisted_tools, skill_ids, knowledge_base_ids, created_at, updated_at, "
                "tool_loading, active_tools) VALUES (:id, :org, :name, 'r', 'p', 'm', '[]', '[]', "
                "'[]', 0, 0, :mode, CAST(:pins AS jsonb))"
            ),
            {
                "id": f"{org}-{name}",
                "org": org,
                "name": name,
                "mode": mode,
                "pins": json.dumps(pins),
            },
        )


async def _modes(manager: DatabaseSessionManager) -> dict[str, str | None]:
    async with manager.session() as session:
        rows = await session.execute(
            text("SELECT name, tool_loading FROM agent_personas ORDER BY name")
        )
        return {row[0]: row[1] for row in rows}


@pytest.mark.asyncio
async def test_agents_with_pinned_tools_keep_loading_only_those_and_the_downgrade_restores_it(
    database_at_047: tuple[str, DatabaseSessionManager],
) -> None:
    url, manager = database_at_047
    await _organization(manager, "quiet", None)
    await _organization(manager, "lean", "on_demand")
    await _organization(manager, "eager", "all")
    for org, name, mode, pins in (
        ("quiet", "own-mode-with-pins", "on_demand", ["file_read"]),
        ("quiet", "own-mode-no-pins", "on_demand", []),
        ("quiet", "inherits-auto-with-pins", None, ["mcp__a__*"]),
        ("quiet", "all-with-pins", "all", ["file_read"]),
        ("lean", "inherits-lean-with-pins", None, ["mcp__a__*"]),
        ("lean", "inherits-lean-no-pins", None, []),
        ("eager", "inherits-all-with-pins", None, ["mcp__a__*"]),
    ):
        await _persona(manager, org, name, mode, pins)

    await _alembic(url, "upgrade", _AFTER)

    assert await _modes(manager) == {
        "own-mode-with-pins": "pinned",
        "own-mode-no-pins": "on_demand",
        "inherits-auto-with-pins": None,
        "all-with-pins": "all",
        "inherits-lean-with-pins": "pinned",
        "inherits-lean-no-pins": None,
        "inherits-all-with-pins": None,
    }

    await _alembic(url, "downgrade", _BEFORE)
    restored = await _modes(manager)
    assert restored["own-mode-with-pins"] == "on_demand"
    assert restored["inherits-lean-with-pins"] == "on_demand", (
        "the downgrade cannot tell them apart"
    )
