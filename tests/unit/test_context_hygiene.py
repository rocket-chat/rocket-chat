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
    assert "[lines 21-470 of 500 hidden;" in clamped
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


async def test_tier3_anchor_compaction_preserves_goals_and_checklists() -> None:
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

    compacted = await cm.compact_with_summary(messages, None, recent_turns_to_preserve=1)

    # Invariant 1: System prompt anchored
    assert compacted[0].role == "system"
    assert "Rocket Chat Engineer" in (compacted[0].content or "")

    # Invariant 2: the request being worked on (the latest one) is anchored verbatim
    assert compacted[1].role == "user"
    assert (compacted[1].content or "").startswith("What is the current status?")

    # Invariant 3: Active checklist preserved in summary
    assert any("Setup Stripe" in (m.content or "") for m in compacted)

    # Invariant 4: Latest turn preserved
    assert compacted[-1].role == "assistant"
    assert "Finishing step 2 now." in (compacted[-1].content or "")


def _step(index: int, calls: int = 1) -> list[ChatMessage]:
    ids = [f"c{index}_{n}" for n in range(calls)]
    return [
        ChatMessage(
            role="assistant",
            tool_calls=[
                {"id": i, "function": {"name": "file_read", "arguments": "{}"}} for i in ids
            ],
        ),
        *(ChatMessage(role="tool", content=f"out {i}", tool_call_id=i) for i in ids),
    ]


async def test_mid_turn_compaction_of_a_long_session_keeps_the_current_request() -> None:
    manager = ContextManager(recent_turns_to_preserve=2)
    messages = [ChatMessage(role="system", content="S")]
    for turn in range(3):
        messages += [ChatMessage(role="user", content=f"old question {turn}"), *_step(turn)]
        messages.append(ChatMessage(role="assistant", content=f"old answer {turn}"))
    messages.append(ChatMessage(role="user", content="CURRENT: fix the login bug"))
    for step in range(6):
        messages += _step(100 + step)
    seen: list[list[ChatMessage]] = []

    async def summarize(request: list[ChatMessage]) -> str:
        seen.append(request)
        return "## Objective\n- fix the login bug\n\n## Next Move\n1. run the login tests" + (
            "\n- detail" * 20
        )

    compacted = await manager.compact_with_summary(messages, summarize)

    assert (compacted[1].content or "").startswith("CURRENT: fix the login bug")
    # The summary is asked on the conversation itself, the summary prompt last.
    assert seen[0][: len(messages)] == messages
    assert (seen[0][-1].content or "").startswith("Summarize the conversation above")
    assert not any("old question 0" == m.content for m in compacted)
    bridge = next(m.content or "" for m in compacted if "[Compacted" in (m.content or ""))
    assert "> old question 0" in bridge, "the person's earlier messages are kept verbatim"
    assert "## Next Move" in bridge


async def test_a_step_with_eight_parallel_calls_is_never_split() -> None:
    manager = ContextManager(recent_turns_to_preserve=1)
    messages = [
        ChatMessage(role="system", content="S"),
        ChatMessage(role="user", content="go"),
        *_step(1),
        *_step(2),
        *_step(3, calls=8),
    ]

    compacted = await manager.compact_with_summary(messages, None)

    tail_tools = [
        m for m in compacted if m.role == "tool" and (m.tool_call_id or "").startswith("c3")
    ]
    assert len(tail_tools) == 8
    assert compacted[-9].role == "assistant" and len(compacted[-9].tool_calls or []) == 8


def _call(call_id: str, name: str, arguments: str) -> dict[str, object]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def test_old_large_tool_arguments_are_pruned_and_stay_valid_json() -> None:
    import json

    cm = ContextManager(recent_turns_to_preserve=1)
    big = json.dumps({"path": "src/app.py", "content": "x = 1\n" * 600})
    small = json.dumps({"path": "a.py", "content": "ok"})
    messages = [
        ChatMessage(role="user", content="Go"),
        ChatMessage(role="assistant", content="", tool_calls=[_call("c1", "file_write", big)]),
        ChatMessage(role="tool", content="ok", tool_call_id="c1"),
        ChatMessage(role="assistant", content="", tool_calls=[_call("c2", "file_write", small)]),
        ChatMessage(role="tool", content="ok", tool_call_id="c2"),
        ChatMessage(role="assistant", content="", tool_calls=[_call("c3", "file_write", big)]),
    ]

    pruned = cm.prune_tool_payloads(messages)

    old = json.loads(pruned[1].tool_calls[0]["function"]["arguments"])  # type: ignore[index]
    assert old["path"] == "src/app.py"
    assert "chars omitted" in old["content"]
    assert pruned[3].tool_calls == messages[3].tool_calls, "small arguments are kept"
    assert pruned[5].tool_calls == messages[5].tool_calls, "the latest step is kept whole"
    assert pruned[1].tool_calls[0]["id"] == "c1"  # type: ignore[index]


def test_a_pruned_result_names_the_tool_and_where_its_full_output_is() -> None:
    cm = ContextManager(recent_turns_to_preserve=1)
    saved = (
        "head\n"
        * 60
        + "... [10 lines omitted. Full output saved to /workspace/.rocket/outputs/2_c1.txt. "
        "Use file_read to inspect middle sections.] ...\n"
    )
    messages = [
        ChatMessage(role="user", content="Go"),
        ChatMessage(role="assistant", content="", tool_calls=[_call("c1", "run_tests", "{}")]),
        ChatMessage(role="tool", content=saved, tool_call_id="c1"),
        ChatMessage(role="assistant", content="", tool_calls=[_call("c2", "bash_exec", "{}")]),
        ChatMessage(role="tool", content="x\n" * 60, tool_call_id="c2"),
        ChatMessage(role="assistant", content="done"),
    ]

    pruned = cm.prune_tool_payloads(messages)

    assert "Tool result of run_tests" in (pruned[2].content or "")
    assert "full output: /workspace/.rocket/outputs/2_c1.txt" in (pruned[2].content or "")
    assert "Tool result of bash_exec" in (pruned[4].content or "")
    assert "full output" not in (pruned[4].content or "")


def test_pruning_keeps_the_fields_of_the_messages_it_rewrites() -> None:
    cm = ContextManager(recent_turns_to_preserve=1)
    messages = [
        ChatMessage(role="user", content="Go"),
        ChatMessage(
            role="assistant",
            content="",
            reasoning="because",
            reasoning_blocks=[{"type": "thinking", "signature": "s"}],
            tool_calls=[_call("c1", "bash_exec", "{}")],
        ),
        ChatMessage(role="tool", content="x\n" * 60, tool_call_id="c1", cacheable=True),
        ChatMessage(role="assistant", content="done"),
    ]

    pruned = cm.prune_tool_payloads(messages)

    assert pruned[2].cacheable is True
    assert pruned[1].reasoning_blocks == [{"type": "thinking", "signature": "s"}]
