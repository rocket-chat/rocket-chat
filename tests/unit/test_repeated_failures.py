"""A call that keeps failing identically is told to stop, without being blocked."""

from __future__ import annotations

import json

from agent_core.repeats import repeat_note, repeated_failure_note, unchanged_repeat_note

from specifications.interfaces.llm import ChatMessage

ARGS = json.dumps({"aggregation": "count_distinct"})
ERROR = 'MCP tool error: Parameter validation failed: "aggregateOn" is required'


def _attempt(call_id: str, result: str, args: str = ARGS, name: str = "agg") -> list[ChatMessage]:
    return [
        ChatMessage(
            role="assistant",
            tool_calls=[
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}
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


def test_verification_calls_across_edits_do_not_ban_tool_when_errors_differ() -> None:
    test_args = json.dumps({"path": "/workspace/repo"})
    err1 = "FAILED tests/test_a.py::test_foo - AssertionError: expected 1 got 0"
    err2 = "FAILED tests/test_b.py::test_bar - KeyError: 'baz'"
    err3 = "FAILED tests/test_c.py::test_qux - TypeError: NoneType object is not callable"

    def _edit(cid: str) -> list[ChatMessage]:
        return [
            ChatMessage(
                role="assistant",
                tool_calls=[
                    {
                        "id": cid,
                        "type": "function",
                        "function": {
                            "name": "file_edit",
                            "arguments": json.dumps(
                                {"path": "foo.py", "replacement_block": "x = 1"}
                            ),
                        },
                    }
                ],
            ),
            ChatMessage(role="tool", content="File edited successfully", tool_call_id=cid),
        ]

    # Attempt 1 -> Edit 1 -> Attempt 2 -> Edit 2 -> Attempt 3
    history = (
        _attempt("t1", err1, test_args, name="run_tests")
        + _edit("e1")
        + _attempt("t2", err2, test_args, name="run_tests")
        + _edit("e2")
    )
    # The 3rd run with a different error should NOT be banned
    note = repeated_failure_note(history, "run_tests", test_args, err3)
    assert note is None


def test_verification_calls_across_edits_flag_stagnant_failure_if_error_digest_is_identical() -> (
    None
):
    test_args = json.dumps({"path": "/workspace/repo"})
    stagnant_err = (
        "FAILED tests/test_core.py::test_login - AssertionError: 401 != 200\n"
        "AssertionError: Expected status code 200 but received 401 Unauthorized"
    )

    def _edit(cid: str) -> list[ChatMessage]:
        return [
            ChatMessage(
                role="assistant",
                tool_calls=[
                    {
                        "id": cid,
                        "type": "function",
                        "function": {
                            "name": "file_edit",
                            "arguments": json.dumps(
                                {"path": "core.py", "replacement_block": "x = 2"}
                            ),
                        },
                    }
                ],
            ),
            ChatMessage(role="tool", content="File edited successfully", tool_call_id=cid),
        ]

    history = (
        _attempt("t1", stagnant_err, test_args, name="run_tests")
        + _edit("e1")
        + _attempt("t2", stagnant_err, test_args, name="run_tests")
        + _edit("e2")
    )
    # The 3rd run with the exact same stagnant error should trigger the stagnant failure note
    note = repeated_failure_note(history, "run_tests", test_args, stagnant_err)
    assert note is not None
    assert "Stagnant failure:" in note
    assert "3 verification runs across edits" in note
    assert "Do not send it again" not in note


def test_verification_calls_without_edits_are_flagged_as_repeated_failure() -> None:
    test_args = json.dumps({"path": "/workspace/repo"})
    err = "FAILED tests/test_core.py::test_login - AssertionError"

    history = _attempt("t1", err, test_args, name="run_tests") + _attempt(
        "t2", err, test_args, name="run_tests"
    )
    note = repeated_failure_note(history, "run_tests", test_args, err)
    assert note is not None
    assert "failed 3 times" in note
    assert "Do not send it again" in note
