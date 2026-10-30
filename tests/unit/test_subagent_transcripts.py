"""Unit tests for subagent transcript persistence and parallel delegation (D1 & D2)."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.tools.registry import ToolRegistry
from api.services.subagent_transcripts import InMemorySubagentTranscriptStore
from sandbox_driver.fake_driver import FakeSandboxDriver
from sandbox_driver.profiles import PlacementProfile, has_parallel_write_capacity

from specifications.interfaces.agent import (
    SubagentTaskResult,
    SubagentTranscriptRecord,
    TurnCallbacks,
    TurnPolicy,
)


def _setup_context(session_id: str = "sess-sub") -> None:
    current_execution_ctx.set(
        ExecutionContext(
            session_id=session_id,
            event_queue=asyncio.Queue(),
            parent_model="test-model",
            tenant_org_id="org-test",
            policy=TurnPolicy(permission_mode="auto"),
            callbacks=TurnCallbacks(),
        )
    )


def test_subagent_task_result_contract() -> None:
    res = SubagentTaskResult(
        role="reviewer",
        task="Audit authentication flow",
        summary="All security checks passed.",
        turns_taken=3,
        tool_calls_count=5,
        duration_ms=1250,
        status="completed",
        artefacts=["report.md"],
        changed_files=["auth.py"],
        tokens=1500,
        cost=0.0045,
    )
    assert res.role == "reviewer"
    assert res.status == "completed"
    assert res.artefacts == ["report.md"]
    assert res.changed_files == ["auth.py"]
    assert res.tokens == 1500
    assert res.cost == 0.0045


async def test_in_memory_subagent_transcript_store() -> None:
    store = InMemorySubagentTranscriptStore()
    now = int(time.time())

    rec1 = SubagentTranscriptRecord(
        id="rec-1",
        session_id="s1",
        org_id="org-1",
        subagent_id="sub-1",
        role="researcher",
        task="Investigate performance",
        summary="Found 2 slow queries",
        status="completed",
        turns_taken=2,
        tool_calls_count=4,
        duration_ms=800,
        tokens=1200,
        cost_usd=0.003,
        changed_files=[],
        artefacts=["perf.json"],
        messages=[{"role": "user", "content": "investigate"}],
        created_at=now,
    )
    rec2 = SubagentTranscriptRecord(
        id="rec-2",
        session_id="s1",
        org_id="org-1",
        subagent_id="sub-2",
        role="coder",
        task="Fix query",
        summary="Added index",
        status="completed",
        turns_taken=1,
        tool_calls_count=2,
        duration_ms=400,
        tokens=600,
        cost_usd=0.0015,
        changed_files=["schema.sql"],
        artefacts=[],
        messages=[{"role": "user", "content": "fix"}],
        created_at=now + 1,
    )

    await store.save_transcript(rec1)
    await store.save_transcript(rec2)

    # List transcripts for session
    transcripts = await store.list_transcripts("s1", "org-1")
    assert len(transcripts) == 2
    assert {t.subagent_id for t in transcripts} == {"sub-1", "sub-2"}

    # Isolation between sessions and orgs
    assert await store.list_transcripts("s2", "org-1") == []
    assert await store.list_transcripts("s1", "org-2") == []

    # Get single transcript
    fetched = await store.get_transcript("s1", "sub-1", "org-1")
    assert fetched is not None
    assert fetched.role == "researcher"
    assert fetched.summary == "Found 2 slow queries"
    assert fetched.tokens == 1200


def test_has_parallel_write_capacity() -> None:
    # None profile -> False
    assert not has_parallel_write_capacity(None)

    # Under-resourced profiles
    assert not has_parallel_write_capacity(
        PlacementProfile(name="tiny", requests={"cpu": "1", "memory": "2Gi"})
    )
    assert not has_parallel_write_capacity(
        PlacementProfile(name="low_ram", requests={"cpu": "4", "memory": "2Gi"})
    )
    assert not has_parallel_write_capacity(
        PlacementProfile(name="low_cpu", requests={"cpu": "1", "memory": "8Gi"})
    )

    # Capacity satisfied: >= 2 CPU, >= 4 GiB
    assert has_parallel_write_capacity(
        PlacementProfile(name="standard", requests={"cpu": "2", "memory": "4Gi"})
    )
    assert has_parallel_write_capacity(
        PlacementProfile(name="beefy", requests={"cpu": "8", "memory": "16Gi"})
    )


async def test_delegate_subagents_parallel_tool() -> None:
    _setup_context("sess-parallel")
    driver = FakeSandboxDriver()
    registry = ToolRegistry(driver=driver)

    mock_runner = AsyncMock()
    mock_runner.run = AsyncMock(
        side_effect=[
            SubagentTaskResult(
                role="service-a-refactor",
                task="Refactor service A",
                summary="Refactored service A",
                turns_taken=2,
                tool_calls_count=3,
                duration_ms=500,
                status="completed",
                artefacts=[],
                changed_files=["service_a.py"],
                tokens=800,
                cost=0.002,
            ),
            SubagentTaskResult(
                role="service-b-refactor",
                task="Refactor service B",
                summary="Refactored service B",
                turns_taken=1,
                tool_calls_count=2,
                duration_ms=400,
                status="completed",
                artefacts=[],
                changed_files=["service_b.py"],
                tokens=600,
                cost=0.0015,
            ),
        ]
    )
    mock_runner.roles = MagicMock(return_value={})
    registry._subagent_runner = mock_runner

    delegations = [
        {"role": "coder", "task": "Refactor service A"},
        {"role": "coder", "task": "Refactor service B"},
    ]

    res = await registry.call_tool("delegate_subagents_parallel", {"delegations": delegations})
    assert "SERVICE-A-REFACTOR" in res
    assert "Refactored service A" in res
    assert "SERVICE-B-REFACTOR" in res
    assert "Refactored service B" in res
    assert mock_runner.run.call_count == 2
