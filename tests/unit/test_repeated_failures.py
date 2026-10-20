"""A call that keeps failing identically is told to stop, without being blocked."""

from __future__ import annotations

import json

from agent_core.repeats import repeat_note, repeated_failure_note, unchanged_repeat_note

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


def test_an_identical_successful_call_with_an_unchanged_result_is_flagged_on_the_third() -> None:
    view = json.dumps({"action": "view", "number": 7})
    reordered = json.dumps({"number": 7, "action": "view"})
    history = [ChatMessage(role="user", content="Ship it")]
    history += _attempt("c1", "#7 open, checks pending", view)
    assert repeat_note(history, "agg", view, "#7 open, checks pending") is None
    history += _attempt("c2", "#7 open, checks pending", view)

    note = repeat_note(history, "agg", reordered, "#7 open, checks pending")

    assert note is not None and "same result 3 times" in note
    assert repeat_note(history, "agg", view, "#7 open, checks green") is None


def test_unchanged_repeats_are_counted_within_the_turn_and_never_for_polling_tools() -> None:
    history = _attempt("c1", "same") + _attempt("c2", "same")
    history.append(ChatMessage(role="user", content="Next request"))

    assert repeat_note(history, "agg", ARGS, "same") is None
    assert (
        unchanged_repeat_note(
            _attempt("c1", "same") + _attempt("c2", "same"), "process_output", ARGS, "same"
        )
        is None
    )
