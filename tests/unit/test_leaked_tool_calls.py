"""A model that writes its tool calls as text is asked to make them, and never shows markup."""

from __future__ import annotations

from typing import Any

from agent_core.leaked_calls import (
    LEAK_GIVE_UP,
    LeakFilter,
    contains_leaked_calls,
    strip_leaked_calls,
)
from turn_harness import run_turn

BAR = "\uff5c"  # DeepSeek's delimiter is a fullwidth vertical bar
DSML = (
    f'\n\n<{BAR}DSML{BAR} calls>\n<{BAR}DSML{BAR} invoke name="update_status">\n'
    f'<{BAR}DSML{BAR} parameter name="status" string="true">Loading</{BAR}DSML{BAR} parameter>\n'
    f"</{BAR}DSML{BAR} invoke>\n</{BAR}DSML{BAR} calls>"
)


def _react_requests(outcome: Any) -> list[Any]:
    """The model calls that take tools: the turn's steps, not its follow-up suggestions."""
    return [request for request in outcome.model.requests if request.tools]


def test_markup_is_recognised_and_what_came_before_it_is_kept() -> None:
    assert contains_leaked_calls(DSML) and contains_leaked_calls("ok <tool_call>{}</tool_call>")
    assert not contains_leaked_calls("a < b and <b>bold</b> and x<y")
    assert strip_leaked_calls("Let me check.\n" + DSML) == "Let me check."


def test_the_stream_filter_hides_markup_split_across_pieces() -> None:
    shown = ""
    stream = LeakFilter()
    for piece in ["Checking ", "<", f"{BAR}DS", f"ML{BAR} calls>", " more", " markup"]:
        shown += stream.feed(piece)
    shown += stream.finish()

    assert shown == "Checking "


def test_ordinary_angle_brackets_are_not_swallowed() -> None:
    shown = ""
    stream = LeakFilter()
    for piece in ["if a ", "<", " b then ", "<", "b>bold</b>", " x<", "y"]:
        shown += stream.feed(piece)
    shown += stream.finish()

    assert shown == "if a < b then <b>bold</b> x<y"


async def test_a_leaked_call_is_discarded_and_the_model_gets_another_go() -> None:
    outcome = await run_turn("leak_recovers", leaks=[DSML], final_text="Here is the answer.")

    steps = _react_requests(outcome)
    assert len(steps) == 2, "one retry after the markup"
    nudge = steps[1].messages[-1]
    assert nudge.role == "user" and "<system-reminder>" in (nudge.content or "")
    assert "wrote tool calls as text" in (nudge.content or "")
    assert not any("DSML" in (m.content or "") for m in steps[1].messages if m is not nudge), (
        "the discarded markup is not in what the model sees next"
    )
    assert "DSML" not in outcome.text_of_events(), "markup never reaches the live stream"
    assert "Here is the answer." in outcome.text_of_events()
    history = " ".join(str(m) for m in outcome.session.conversation_history)
    assert "DSML" not in history and "Here is the answer." in history


async def test_a_model_that_keeps_leaking_ends_with_a_clear_message_not_markup() -> None:
    outcome = await run_turn("leak_persists", leaks=[DSML, DSML, DSML], final_text="unreached")

    assert len(_react_requests(outcome)) == 3, "two retries, then it stops"
    shown = outcome.text_of_events() + " ".join(
        str(m) for m in outcome.session.conversation_history
    )
    assert LEAK_GIVE_UP in shown and "DSML" not in shown and "unreached" not in shown
