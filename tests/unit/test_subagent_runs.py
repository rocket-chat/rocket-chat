"""Background subagents as supervised runs: leases, a lost replica, and results read back."""

from __future__ import annotations

import time

from agent_core.subagent_report import render_for_parent
from agent_core.subagents import result_from_dict, result_to_dict
from api.services.subagent_runs import (
    LOST_REPLICA_SUMMARY,
    InMemorySubagentRunStore,
)
from api.services.turn_trace import PROCESS_INSTANCE_ID

from specifications.interfaces.agent import (
    SubagentFinding,
    SubagentReport,
    SubagentRunRecord,
    SubagentTaskResult,
)


def _run(subagent_id: str, status: str = "running") -> SubagentRunRecord:
    now = int(time.time())
    return SubagentRunRecord(
        id=f"run_{subagent_id}",
        org_id="org_runs",
        session_id="s1",
        subagent_id=subagent_id,
        role="researcher",
        task="Look into it",
        status=status,
        created_at=now,
        updated_at=now,
    )


async def test_an_active_run_is_leased_to_this_replica_and_a_finished_one_is_not() -> None:
    store = InMemorySubagentRunStore()
    await store.save_run(_run("a"))
    await store.save_run(_run("b", status="completed"))

    active = await store.get_run("org_runs", "s1", "a")
    done = await store.get_run("org_runs", "s1", "b")
    assert active is not None and active.lease_owner == PROCESS_INSTANCE_ID
    assert active.lease_expires_at > time.time()
    assert done is not None and done.lease_owner == "" and done.lease_expires_at == 0


async def test_a_run_whose_replica_went_silent_is_settled_as_failed_once() -> None:
    store = InMemorySubagentRunStore()
    await store.save_run(_run("lost"))
    now = int(time.time()) + 10_000

    lost = await store.claim_expired("another-replica", now, now + 90)
    again = await store.claim_expired("another-replica", now, now + 90)

    assert [r.subagent_id for r in lost] == ["lost"] and again == []
    settled = await store.get_run("org_runs", "s1", "lost")
    assert settled is not None and settled.status == "failed"
    assert settled.result is not None
    assert result_from_dict(settled.result).summary == LOST_REPLICA_SUMMARY


async def test_renewing_extends_only_this_replicas_active_leases() -> None:
    store = InMemorySubagentRunStore()
    await store.save_run(_run("mine"))
    until = int(time.time()) + 3_600

    assert await store.renew_leases(PROCESS_INSTANCE_ID, until) == 1
    assert await store.renew_leases("someone-else", until) == 0
    run = await store.get_run("org_runs", "s1", "mine")
    assert run is not None and run.lease_expires_at == until


def test_a_stored_result_renders_like_the_live_one() -> None:
    result = SubagentTaskResult(
        role="reviewer",
        task="Review",
        summary="One bug.",
        status="completed",
        subagent_id="sub_1",
        report=SubagentReport(
            status="completed",
            summary="One bug.",
            findings=[SubagentFinding(file="a.py", line=3, severity="high", evidence="boom")],
        ),
    )
    restored = result_from_dict(result_to_dict(result))
    assert render_for_parent(restored) == render_for_parent(result)
    assert "a.py:3" in render_for_parent(restored)
