"""Unit tests for phase-adaptive thinking budget modulation."""

from __future__ import annotations

import asyncio

from agent_core.orchestrator import _TurnRun, step_thinking_budget

from specifications.interfaces.llm import ChatMessage


def _make_run(
    step: int = 1,
    notes: list[str] | None = None,
    conversation: list[ChatMessage] | None = None,
) -> _TurnRun:
    return _TurnRun(
        session_id="s1",
        org_id="o1",
        conversation=conversation or [],
        seed_messages=[],
        react_model="m1",
        summary_model="m1",
        turn_limit=25,
        event_queue=asyncio.Queue(),
        whitelisted_tools=None,
        exec_ctx=None,
        pending_attachments=[],
        step=step,
        next_notes=notes or [],
    )


def test_thinking_budget_step_zero_full() -> None:
    run = _make_run(step=0)
    assert step_thinking_budget(run, 16000) == 16000


def test_thinking_budget_with_notes_full() -> None:
    run = _make_run(step=2, notes=["Please check tests"])
    assert step_thinking_budget(run, 16000) == 16000


def test_thinking_budget_small_budget_untouched() -> None:
    run = _make_run(step=2)
    assert step_thinking_budget(run, 2048) == 2048
    assert step_thinking_budget(run, None) is None


def test_thinking_budget_scales_down_on_routine_reads() -> None:
    conv = [
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "c1", "name": "file_read", "arguments": "{}"}],
        ),
        ChatMessage(role="tool", content="file content", tool_call_id="c1"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "c2", "name": "grep_search", "arguments": "{}"}],
        ),
        ChatMessage(role="tool", content="match found", tool_call_id="c2"),
    ]
    run = _make_run(step=3, conversation=conv)
    assert step_thinking_budget(run, 16000) == 4096


def test_thinking_budget_full_on_error() -> None:
    conv = [
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "c1", "name": "file_read", "arguments": "{}"}],
        ),
        ChatMessage(role="tool", content="Error: File not found", tool_call_id="c1"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "c2", "name": "grep_search", "arguments": "{}"}],
        ),
        ChatMessage(role="tool", content="match found", tool_call_id="c2"),
    ]
    run = _make_run(step=3, conversation=conv)
    assert step_thinking_budget(run, 16000) == 16000


def test_thinking_budget_full_on_mutation() -> None:
    conv = [
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "c1", "name": "file_write", "arguments": "{}"}],
        ),
        ChatMessage(role="tool", content="Success", tool_call_id="c1"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "c2", "name": "file_read", "arguments": "{}"}],
        ),
        ChatMessage(role="tool", content="content", tool_call_id="c2"),
    ]
    run = _make_run(step=3, conversation=conv)
    assert step_thinking_budget(run, 16000) == 16000
