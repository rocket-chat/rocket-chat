"""A call that keeps failing identically is told to stop, without being blocked."""

from __future__ import annotations

import json

from agent_core.repeats import repeated_failure_note

from specifications.interfaces.llm import ChatMessage

ARGS = json.dumps({"aggregation": "count_distinct"})
ERROR = 'MCP tool error: Parameter validation failed: "aggregateOn" is required'


def _attempt(call_id: str, result: str, args: str = ARGS) -> list[ChatMessage]:
    return [
        ChatMessage(
            role="assistant",
            tool_calls=[
                {"id": call_id, "type": "function", "function": {"name": "agg", "arguments": args}}
            ],
        ),
        ChatMessage(role="tool", content=result, tool_call_id=call_id),
    ]


def test_the_third_identical_failure_is_flagged_but_the_second_is_not() -> None:
    history = _attempt("c1", ERROR)

    assert repeated_failure_note(history, "agg", ARGS, ERROR) is None
    history += _attempt("c2", ERROR)
    note = repeated_failure_note(history, "agg", ARGS, ERROR)
    assert note is not None and "failed 3 times" in note and "Do not send it again" in note


def test_changed_arguments_or_a_success_reset_the_count() -> None:
    history = _attempt("c1", ERROR) + _attempt("c2", ERROR)

    assert repeated_failure_note(history, "agg", '{"aggregation":"count"}', ERROR) is None
    assert repeated_failure_note(history, "agg", ARGS, '{"status":"success"}') is None
    assert (
        repeated_failure_note(_attempt("c1", ERROR) + _attempt("c2", "ok"), "agg", ARGS, ERROR)
        is None
    )
