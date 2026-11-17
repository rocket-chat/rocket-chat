"""session_agents under PostgreSQL row-level security: another organization lists nothing."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from api.db.models import OrganizationModel, SessionModel, UserModel
from api.db.session import DatabaseSessionManager
from api.services.agent_sessions import PostgresSessionAgents
from api.store import InMemorySessionStore

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Requires disposable test PostgreSQL with RLS")
@pytest.mark.asyncio
async def test_participation_is_isolated_per_organization_and_skips_private_sessions() -> None:
    assert TEST_DATABASE_URL is not None
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    org_a, org_b = (f"org_{uuid.uuid4().hex[:12]}" for _ in range(2))
    public, private = (f"sess_{uuid.uuid4().hex[:12]}" for _ in range(2))
    now = int(time.time())
    async with db.session(tenant_org_id=org_a) as s:
        s.add(OrganizationModel(id=org_a, name="A", slug=org_a, created_at=now))
        s.add(UserModel(id=f"u_{org_a}", org_id=org_a, email="a@t.com", name="A", created_at=now))
        s.add(
            SessionModel(
                id=public, org_id=org_a, user_id=f"u_{org_a}", title="Trips", created_at=now
            )
        )
        s.add(
            SessionModel(
                id=private,
                org_id=org_a,
                user_id=f"u_{org_a}",
                title="Mine",
                created_at=now,
                metadata_json={"slack_is_dm": True},
            )
        )
    participation = PostgresSessionAgents(db)
    for session_id in (public, private):
        await participation.record(org_a, session_id, "agent_data", now)
    await participation.record(org_a, public, "agent_data", now + 5)

    listed = await participation.summaries(org_a, "agent_data", "", "", 10, InMemorySessionStore())

    assert [s.session_id for s in listed] == [public]
    assert await participation.answered(org_a, public, "agent_data")
    assert not await participation.answered(org_b, public, "agent_data")
    assert (
        await participation.summaries(org_b, "agent_data", "", "", 10, InMemorySessionStore()) == []
    )
