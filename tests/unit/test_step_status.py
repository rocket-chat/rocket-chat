"""The status line of a step that runs tools is derived from the tools, not the model's text."""

from __future__ import annotations

from agent_core.step_status import describe_step

from tests.turn_harness import run_turn


def _calls(*names: str) -> dict[int, dict[str, object]]:
    return {index: {"name": name, "arguments": "{}"} for index, name in enumerate(names)}


def test_known_tools_read_as_an_action() -> None:
    assert describe_step(_calls("bash_exec")) == "Running a command..."
    assert describe_step(_calls("file_read")) == "Reading files..."


def test_repeated_tools_are_named_once_and_many_are_counted() -> None:
    assert describe_step(_calls("file_read", "file_read", "file_read")) == "Reading files..."
    assert (
        describe_step(_calls("file_read", "grep_search", "bash_exec", "web_search"))
        == "Reading files, Searching the code and 2 more..."
    )


def test_unknown_and_mcp_tools_fall_back_to_a_readable_name() -> None:
    assert describe_step(_calls("frobnicate_all")) == "Using frobnicate all..."
    assert describe_step(_calls("mcp__signoz__search_logs")) == "Using search logs (signoz)..."


def test_no_calls_gives_no_status() -> None:
    assert describe_step({}) == ""


async def test_a_step_with_a_preface_publishes_the_tool_status_not_the_preface() -> None:
    preface = "Now let me read the test file and the Makefile."
    outcome = await run_turn(
        "org_step_status",
        steps=[("say_and_call", preface, "list_dir", {"path": "."}), ("say", "Done.")],
    )

    messages = [
        e.payload["message"]
        for e in outcome.events_of("status_changed")
        if e.payload.get("status") == "running" and e.payload.get("message")
    ]
    assert "Listing files..." in messages
    assert preface not in messages
    # The preface is still part of the conversation: only the status stopped repeating it.
    streamed = "".join(str(e.payload.get("delta") or "") for e in outcome.events_of("token"))
    assert preface in streamed
