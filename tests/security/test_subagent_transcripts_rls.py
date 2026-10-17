"""Security and isolation tests for subagent_transcripts table with PostgreSQL Row-Level Security."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from api.db.models import OrganizationModel, SessionModel, UserModel
from api.db.session import DatabaseSessionManager
from api.services.subagent_transcripts import (
    InMemorySubagentTranscriptStore,
    PostgresSubagentTranscriptStore,
)

from specifications.interfaces.agent import SubagentTranscriptRecord

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.asyncio
async def test_subagent_transcripts_tenant_isolation_in_memory() -> None:
    store = InMemorySubagentTranscriptStore()
    rec = SubagentTranscriptRecord(
        id=f"rec_{uuid.uuid4().hex[:16]}",
        session_id="s1",
        org_id="org_a",
        subagent_id="sub_1",
        role="Research Assistant",
        task="Analyze codebase",
        summary="Analysis complete",
        status="completed",
        turns_taken=3,
        tool_calls_count=5,
        duration_ms=2500,
        tokens=1500,
        cost_usd=0.015,
        changed_files=[],
        artefacts=[],
        messages=[],
        created_at=int(time.time()),
    )
    await store.save_transcript(rec)

    assert await store.get_transcript("s1", "sub_1", "org_b") is None
    assert len(await store.list_transcripts("s1", "org_b")) == 0

    loaded_a = await store.get_transcript("s1", "sub_1", "org_a")
    assert loaded_a is not None
    assert loaded_a.task == "Analyze codebase"
    assert len(await store.list_transcripts("s1", "org_a")) == 1


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Requires disposable test PostgreSQL with RLS")
@pytest.mark.asyncio
async def test_subagent_transcripts_tenant_isolation_postgres() -> None:
    db = DatabaseSessionManager(TEST_DATABASE_URL)
    org_a = f"org_{uuid.uuid4().hex[:12]}"
    org_b = f"org_{uuid.uuid4().hex[:12]}"
    session_id = f"sess_{uuid.uuid4().hex[:12]}"

    now = int(time.time())
    async with db.session(tenant_org_id=org_a) as s:
        s.add(OrganizationModel(id=org_a, name="Org A", slug=org_a, created_at=now))
        s.add(
            UserModel(
                id=f"u_{org_a}", org_id=org_a, email="a@test.com", name="User A", created_at=now
            )
        )
        s.add(
            SessionModel(
                id=session_id, org_id=org_a, user_id=f"u_{org_a}", title="Session A", created_at=now
            )
        )

    async with db.session(tenant_org_id=org_b) as s:
        s.add(OrganizationModel(id=org_b, name="Org B", slug=org_b, created_at=now))
        s.add(
            UserModel(
                id=f"u_{org_b}", org_id=org_b, email="b@test.com", name="User B", created_at=now
            )
        )

    store = PostgresSubagentTranscriptStore(db)
    rec = SubagentTranscriptRecord(
        id=f"rec_{uuid.uuid4().hex[:16]}",
        session_id=session_id,
        org_id=org_a,
        subagent_id="sub_tenant_a",
        role="Security Auditor",
        task="Confidential audit in Org A",
        summary="Found 0 vulnerabilities",
        status="completed",
        turns_taken=2,
        tool_calls_count=4,
        duration_ms=1800,
        tokens=1200,
        cost_usd=0.012,
        changed_files=["security_report.md"],
        artefacts=[],
        messages=[],
        created_at=now,
    )
    await store.save_transcript(rec)

    # Org B must see nothing under Row-Level Security
    assert await store.get_transcript(session_id, "sub_tenant_a", org_b) is None
    assert len(await store.list_transcripts(session_id, org_b)) == 0

    # Org A sees the transcript
    loaded = await store.get_transcript(session_id, "sub_tenant_a", org_a)
    assert loaded is not None
    assert loaded.task == "Confidential audit in Org A"
    assert len(await store.list_transcripts(session_id, org_a)) == 1
