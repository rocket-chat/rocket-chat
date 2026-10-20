"""A reloaded session shows each thought between the steps it led to, as it streamed."""

from __future__ import annotations

from turn_harness import run_turn


async def test_each_steps_reasoning_is_saved_with_the_call_it_led_to() -> None:
    outcome = await run_turn(
        "org_step_reasoning",
        steps=[
            ("think_and_call", "First look at the repository layout.", "list_dir", {"path": "."}),
            ("think_and_call", "Now the sources.", "list_dir", {"path": "src"}),
            ("think", "stop", "Both listings are enough to answer."),
            ("say", "Two folders."),
        ],
    )

    answer = outcome.session.conversation_history[-1]
    calls = answer.tool_calls or []
    assert [c.get("reasoning") for c in calls] == [
        "First look at the repository layout.",
        "Now the sources.",
    ]
    assert "First look" not in (answer.reasoning or ""), "never saved twice"
    assert "Both listings" in (answer.reasoning or ""), "the reasoning after the last call"
