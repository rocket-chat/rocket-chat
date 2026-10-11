"""Persona storage against PostgreSQL row-level security."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from api.db.session import DatabaseSessionManager
from api.models import AgentPersonaModel
from api.services.personas import get_persona, list_personas, save_persona

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL not configured")


@pytest.fixture
async def manager(tenant_rows: None) -> AsyncIterator[DatabaseSessionManager]:
    db = DatabaseSessionManager(TEST_DB_URL)
    yield db
    await db.close()


def _persona(persona_id: str) -> AgentPersonaModel:
    return AgentPersonaModel(id=persona_id, name="n", role_title="r", system_prompt="s")


@pytest.mark.asyncio
async def test_an_id_taken_by_another_organization_is_replaced_not_overwritten(
    manager: DatabaseSessionManager,
) -> None:
    first = await save_persona(manager, "test_org", "admin_user", True, _persona("pg-shared-id"))
    second = await save_persona(manager, "usage_org", "usage_user", True, _persona("pg-shared-id"))

    assert first.id == "pg-shared-id"
    assert second.id != "pg-shared-id" and second.id.startswith("agent-")
    assert await get_persona(manager, "usage_org", "usage_user", "pg-shared-id") is None
    assert (await get_persona(manager, "test_org", "admin_user", "pg-shared-id")) is not None
    assert second.id not in {p.id for p in await list_personas(manager, "test_org", "admin_user")}


@pytest.mark.asyncio
async def test_personal_personas_are_private_and_edits_apply_immediately(
    manager: DatabaseSessionManager,
) -> None:
    mine = _persona("pg-personal")
    mine.scope = "user"
    await save_persona(manager, "test_org", "member_user", False, mine)

    assert await get_persona(manager, "test_org", "member_user", "pg-personal") is not None
    assert await get_persona(manager, "test_org", "user_1", "pg-personal") is None

    mine.name = "renamed"
    await save_persona(manager, "test_org", "member_user", False, mine)
    reread = await get_persona(manager, "test_org", "member_user", "pg-personal")
    assert reread is not None and reread.name == "renamed"


@pytest.mark.asyncio
async def test_subagent_roles_are_stored_per_organization(
    manager: DatabaseSessionManager,
) -> None:
    from api.models import SubagentConfigModel
    from api.services.subagents import load_subagent_configs, save_subagent_config

    role = SubagentConfigModel(
        id="pg-role", name="n", role_title="r", description="d", system_prompt="p", max_turns=2
    )
    await save_subagent_config(manager, "test_org", role)
    await save_subagent_config(manager, "usage_org", role.model_copy(update={"max_turns": 7}))

    assert (await load_subagent_configs(manager, "test_org"))["pg-role"].max_turns == 2
    assert (await load_subagent_configs(manager, "usage_org"))["pg-role"].max_turns == 7
    assert "researcher" in await load_subagent_configs(manager, "test_org"), "defaults remain"
    await save_subagent_config(manager, "test_org", role.model_copy(update={"max_turns": 3}))
    assert (await load_subagent_configs(manager, "test_org"))["pg-role"].max_turns == 3
