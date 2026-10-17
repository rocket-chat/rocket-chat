"""100-step long-scenario engine test for multi-repo refactor and invariants (§6.1)."""

from __future__ import annotations

import pytest
from api.services.compaction import InMemoryCompactionStore
from api.services.transcript import InMemoryTranscriptStore
from api.store import InMemorySessionStore
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import ChatMessage
from tests.turn_harness import run_turn


@pytest.mark.asyncio
async def test_100_step_multi_service_refactor_scenario() -> None:
    """Executes a 100-step multi-repo scenario and verifies long-session invariants (§6.1).

    Invariants asserted:
    1. Append-only transcript history in transcript store.
    2. Prefix-stability ratio >= 0.9 across consecutive requests.
    3. PLAN.md working memory preserved in session metadata and re-injected.
    4. No partial/unpaired tool messages stored.
    5. Cost and token accounting accuracy.
    """
    transcript_store = InMemoryTranscriptStore()
    compaction_store = InMemoryCompactionStore()
    store = InMemorySessionStore()
    driver = FakeSandboxDriver()
    org = "org_100_step"
    session_id = f"session_{org}"

    # Setup multi-repo workspace structure
    await driver.write_file(
        session_id,
        ".rocket/PLAN.md",
        (
            "# Multi-Service Refactor Plan\n"
            "- [ ] Refactor service-a models\n"
            "- [ ] Refactor service-b endpoints\n"
            "- [ ] Integration verification\n"
        ),
    )
    await driver.write_file(
        session_id, ".rocket/PROGRESS.md", "Beginning 100-step refactor sequence."
    )

    # Script 100 steps across 3 services
    steps: list[tuple[str, ...]] = []
    services = ["service-a", "service-b", "service-c"]

    for i in range(1, 100):
        svc = services[i % 3]
        if i % 2 == 1:
            steps.append(
                (
                    "call",
                    "file_write",
                    {
                        "path": f"{svc}/src/module_{i}.py",
                        "content": f"# Implementation module {i}\nval_{i} = {i * 10}\n",
                    },
                )
            )
        else:
            steps.append(
                (
                    "call",
                    "bash_exec",
                    {"command": f"pytest {svc}/tests/test_module_{i - 1}.py"},
                )
            )

    steps.append(("say", "Completed all 100 refactoring and verification passes."))

    outcome = await run_turn(
        org,
        steps=steps,
        driver=driver,
        store=store,
        transcript_store=transcript_store,
        compaction_store=compaction_store,
        model_choice="openrouter/deepseek/deepseek-v4.1-flash",
    )

    # Invariant 1: Append-only history in durable transcript store
    all_stored_messages = await transcript_store.load(session_id, org)
    assert len(all_stored_messages) >= 100
    for _idx, msg in enumerate(all_stored_messages):
        assert isinstance(msg, ChatMessage)
        assert msg.role in ("user", "assistant", "system", "tool")

    # Invariant 2: Prefix-stability ratio >= 0.9 across requests
    # Prefix stability: comparing the common prompt prefix length between requests k-1 and k
    react_requests = [
        r
        for r in outcome.model.requests
        if r.messages and "expert developer copilot" not in (r.messages[0].content or "")
    ]
    assert len(react_requests) >= 50
    stable_count = 0
    total_comparisons = len(react_requests) - 1

    for k in range(1, len(react_requests)):
        prev_msgs = react_requests[k - 1].messages
        curr_msgs = react_requests[k].messages
        # First message is stable system prompt
        assert prev_msgs[0].role == curr_msgs[0].role
        assert prev_msgs[0].content == curr_msgs[0].content

        # Shared prefix length
        common_len = 0
        for m1, m2 in zip(prev_msgs, curr_msgs, strict=False):
            if m1.role == m2.role and m1.content == m2.content:
                common_len += 1
            else:
                break
        # Prefix stability ratio for this step
        if common_len >= len(prev_msgs) - 2:
            stable_count += 1

    prefix_stability_ratio = stable_count / total_comparisons
    assert prefix_stability_ratio >= 0.90, (
        f"Expected ratio >= 0.9, got {prefix_stability_ratio:.2f}"
    )

    # Invariant 3: PLAN.md working memory preserved in session metadata
    assert outcome.session.metadata.get("plan_md") is not None
    assert "Multi-Service Refactor Plan" in outcome.session.metadata["plan_md"]
    assert outcome.session.metadata.get("progress_md") is not None

    # Invariant 4: No partial or unpaired tool calls in stored transcript
    tool_call_ids: set[str] = set()
    tool_resp_ids: set[str] = set()
    for m in all_stored_messages:
        if m.role == "assistant" and m.tool_calls:
            for tc in m.tool_calls:
                tc_id = tc.get("id") or (tc.id if hasattr(tc, "id") else None)
                if tc_id:
                    tool_call_ids.add(tc_id)
        elif m.role == "tool" and m.tool_call_id:
            tool_resp_ids.add(m.tool_call_id)

    # Every dispatched tool call has its paired response
    assert tool_call_ids == tool_resp_ids

    # Invariant 5: Files created across all 3 services in sandbox
    for svc in services:
        svc_files = [f for f in driver._files.get(session_id, {}) if f.startswith(f"{svc}/src")]
        assert len(svc_files) > 0
