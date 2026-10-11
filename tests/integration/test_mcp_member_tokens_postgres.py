"""Per-member MCP tokens on real PostgreSQL: row-level security and cascade."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator

import pytest
from api.db.models import McpServerEntityModel, OrganizationModel, UserModel
from api.db.session import DatabaseSessionManager
from api.services.mcp_member_tokens import McpMemberTokenStore

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")
ADMIN_DB_URL = os.getenv("ADMIN_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not (TEST_DB_URL and ADMIN_DB_URL), reason="TEST/ADMIN_DATABASE_URL not configured"
)


@pytest.fixture
async def dbs() -> AsyncIterator[tuple[DatabaseSessionManager, DatabaseSessionManager]]:
    app_db, admin_db = DatabaseSessionManager(TEST_DB_URL), DatabaseSessionManager(ADMIN_DB_URL)
    yield app_db, admin_db
    await app_db.close()
    await admin_db.close()


async def _org_with_server(admin: DatabaseSessionManager) -> tuple[str, str, str, str]:
    tag = uuid.uuid4().hex[:8]
    org, alice, bob, server = f"org_{tag}", f"alice_{tag}", f"bob_{tag}", f"srv_{tag}"
    async with admin.session() as session:
        session.add(OrganizationModel(id=org, name=org, slug=org.replace("_", "-")))
        await session.flush()
        for user in (alice, bob):
            session.add(UserModel(id=user, org_id=org, email=f"{user}@x.test", name=user))
        await session.flush()
        session.add(
            McpServerEntityModel(
                id=server,
                org_id=org,
                scope="org",
                name="Linear",
                transport="http",
                endpoint_or_command="https://mcp.example/mcp",
                created_at=int(time.time()),
                updated_at=int(time.time()),
            )
        )
    return org, server, alice, bob


async def test_tokens_are_per_member_and_invisible_across_organizations(
    dbs: tuple[DatabaseSessionManager, DatabaseSessionManager],
) -> None:
    app_db, admin_db = dbs
    org, server, alice, bob = await _org_with_server(admin_db)
    other_org, *_ = await _org_with_server(admin_db)
    store = McpMemberTokenStore(app_db)

    await store.put(org, server, alice, {"access_token_enc": "enc-alice"})
    await store.put(org, server, alice, {"access_token_enc": "enc-alice-2"})  # replaces
    await store.put(org, server, bob, {"access_token_enc": "enc-bob"})

    assert (await store.get(org, server, alice) or {})["access_token_enc"] == "enc-alice-2"
    assert (await store.get(org, server, bob) or {})["access_token_enc"] == "enc-bob"
    assert await store.connected_servers(org, alice) == {server}
    assert await store.get(other_org, server, alice) is None, "another tenant sees nothing"
    assert await store.delete(other_org, server, alice) is False
    assert await store.delete(org, server, alice) is True
    assert await store.get(org, server, alice) is None
    assert await store.get(org, server, bob) is not None


async def test_deleting_the_server_removes_every_members_tokens(
    dbs: tuple[DatabaseSessionManager, DatabaseSessionManager],
) -> None:
    app_db, admin_db = dbs
    org, server, alice, _ = await _org_with_server(admin_db)
    store = McpMemberTokenStore(app_db)
    await store.put(org, server, alice, {"access_token_enc": "enc"})

    async with admin_db.session() as session:
        row = await session.get(McpServerEntityModel, server)
        assert row is not None
        await session.delete(row)

    assert await store.get(org, server, alice) is None
