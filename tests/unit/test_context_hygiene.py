"""
Unit tests for 3-tier context hygiene:
- Tier 1: Deterministic head/tail output clamping.
- Tier 2: Tool-payload pruning.
- Tier 3: Invariant anchor compaction.
"""

from __future__ import annotations

from agent_core.context import ContextManager
from agent_core.tools.clamp import clamp_output

from specifications.interfaces.llm import ChatMessage


def test_clamp_output_preserves_short_content() -> None:
    short_text = "Line 1\nLine 2\nLine 3\n"
    clamped = clamp_output(short_text, max_lines=10, max_bytes=1000)
    assert clamped == short_text


def test_clamp_output_head_tail_slice() -> None:
    # 500 lines of mock log
    lines = [f"Log entry line {i:04d}: something happened\n" for i in range(1, 501)]
    full_text = "".join(lines)

    clamped = clamp_output(
        full_text,
        max_lines=100,
        max_bytes=50_000,
        head_lines=20,
        tail_lines=30,
    )

    clamped_lines = clamped.splitlines()
    # First line should be line 1
    assert "Log entry line 0001" in clamped_lines[0]
    # Line 20 should be in head
    assert "Log entry line 0020" in clamped
    # Line 21-470 should be truncated
    assert "Log entry line 0050" not in clamped
    # Marker should be present
    assert "TRUNCATED 450 lines" in clamped
    # Tail lines should be present
    assert "Log entry line 0500" in clamped_lines[-1]


def test_tier2_tool_payload_pruning() -> None:
    cm = ContextManager(recent_turns_to_preserve=2)

    messages = [
        ChatMessage(role="user", content="Build the app"),
        ChatMessage(
            role="assistant",
            content="Running build",
            tool_calls=[{"id": "call_1", "function": {"name": "bash_exec"}}],
        ),
        ChatMessage(
            role="tool",
            content="Building...\n" * 50,  # 50 lines of output
            tool_call_id="call_1",
        ),
        ChatMessage(
            role="assistant",
            content="Running tests",
            tool_calls=[{"id": "call_2", "function": {"name": "bash_exec"}}],
        ),
        ChatMessage(
            role="tool",
            content="Running tests...\n" * 50,
            tool_call_id="call_2",
        ),
        ChatMessage(role="assistant", content="Analyzing results..."),
        ChatMessage(role="user", content="Next command"),
        ChatMessage(
            role="assistant",
            content="Checking status",
            tool_calls=[{"id": "call_3", "function": {"name": "bash_exec"}}],
        ),
        ChatMessage(
            role="tool",
            content="Status: ok\n" * 50,
            tool_call_id="call_3",
        ),
    ]

    pruned = cm.prune_tool_payloads(messages, recent_turns_to_preserve=2)

    assert len(pruned) == len(messages)
    # The oldest tool output (call_1) should be pruned to a receipt
    assert "pruned to preserve active reasoning context" in (pruned[2].content or "")
    assert pruned[2].tool_call_id == "call_1"
    # The newest tool output in the preserved tail should NOT be pruned
    assert "Status: ok" in (pruned[8].content or "")


def test_tier3_anchor_compaction_preserves_goals_and_checklists() -> None:
    cm = ContextManager(recent_turns_to_preserve=1)

    messages = [
        ChatMessage(role="system", content="You are Rocket Chat Engineer."),
        ChatMessage(role="user", content="Goal: Build autonomous payment gateway."),
        ChatMessage(
            role="assistant",
            content="Creating plan",
            tool_calls=[
                {
                    "id": "plan_1",
                    "function": {
                        "name": "update_task_checklist",
                        "arguments": '[{"id": "1", "title": "Setup Stripe", "status": "completed"}]',
                    },
                }
            ],
        ),
        ChatMessage(role="tool", content="Checklist updated", tool_call_id="plan_1"),
        ChatMessage(role="assistant", content="Step 1 done. Proceeding."),
        ChatMessage(role="user", content="Continue."),
        ChatMessage(role="assistant", content="Working on step 2."),
        ChatMessage(role="user", content="What is the current status?"),
        ChatMessage(role="assistant", content="Finishing step 2 now."),
    ]

    compacted = cm.compact_with_anchors(messages, recent_turns_to_preserve=1)

    # Invariant 1: System prompt anchored
    assert compacted[0].role == "system"
    assert "Rocket Chat Engineer" in (compacted[0].content or "")

    # Invariant 2: Initial user prompt anchored
    assert compacted[1].role == "user"
    assert "Goal: Build autonomous payment gateway." in (compacted[1].content or "")

    # Invariant 3: Active checklist preserved in summary
    assert any("Setup Stripe" in (m.content or "") for m in compacted)

    # Invariant 4: Latest turn preserved
    assert compacted[-1].role == "assistant"
    assert "Finishing step 2 now." in (compacted[-1].content or "")
