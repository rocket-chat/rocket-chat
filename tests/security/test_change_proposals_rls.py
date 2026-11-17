"""change_proposals under PostgreSQL row-level security: another organization sees nothing."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from api.db.models import OrganizationModel
from api.db.session import DatabaseSessionManager
from api.services.proposals import PostgresProposalStore

from specifications.interfaces.proposals import ChangeProposal, ProposalEdit

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Requires disposable test PostgreSQL with RLS")
@pytest.mark.asyncio
async def test_proposals_are_isolated_and_decided_once() -> None:
    assert TEST_DATABASE_URL is not None
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    org_a, org_b = (f"org_{uuid.uuid4().hex[:12]}" for _ in range(2))
    async with db.session(tenant_org_id=org_a) as s:
        s.add(OrganizationModel(id=org_a, name="A", slug=org_a, created_at=int(time.time())))
    store = PostgresProposalStore(db)
    proposal = await store.create(
        ChangeProposal(
            id=f"prop_{uuid.uuid4().hex[:12]}",
            org_id=org_a,
            agent_id="agent_data",
            target_kind="instructions",
            target_id="agent_data",
            target_name="Data assistant",
            summary="s",
            rationale="r",
            base_content="a",
            proposed_content="b",
            edits=[ProposalEdit("a", "b")],
        )
    )

    assert await store.get(org_b, proposal.id) is None
    assert await store.list_for_org(org_b, status=None, agent_id=None, limit=5) == []
    assert await store.count_pending(org_a, "agent_data") == 1
    decided = await store.decide(
        org_a, proposal.id, status="approved", decided_by="u1", note="", applied_content="b"
    )
    assert decided is not None and decided.edits == [ProposalEdit("a", "b")]
    again = await store.decide(
        org_a, proposal.id, status="rejected", decided_by="u1", note="", applied_content=None
    )
    assert again is None
