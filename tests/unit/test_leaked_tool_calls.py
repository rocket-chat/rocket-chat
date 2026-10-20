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


# What deepseek-v4-pro wrote in production: plain tags, plural, no DeepSeek delimiters.
PLAIN = (
    '<tool_calls>\n<invoke name="update_status">\n<parameter name="status">Recherche des logs'
    '</parameter>\n</invoke>\n<invoke name="execute_skill">\n<parameter name="skill">'
    "signoz-logs-traces</parameter>\n</invoke>\n</tool_calls>"
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


def test_plain_plural_and_attributed_tags_are_markup_too() -> None:
    for text in (PLAIN, '<tool_call name="x">{}</tool_call>', "<function_calls>\n<invoke"):
        assert contains_leaked_calls(text), text
        assert strip_leaked_calls(text) == ""


def test_the_stream_filter_shows_nothing_of_a_plain_tool_calls_block() -> None:
    shown = ""
    stream = LeakFilter()
    for piece in ["<tool", "_calls", ">\n", "<inv", 'oke name="update_status">', "</tool_calls>"]:
        shown += stream.feed(piece)
    shown += stream.finish()

    assert shown == ""


async def test_repeated_plain_markup_ends_with_only_the_clear_message() -> None:
    outcome = await run_turn("leak_plain", leaks=[PLAIN, PLAIN, PLAIN], final_text="unreached")

    shown = outcome.text_of_events() + " ".join(
        str(m) for m in outcome.session.conversation_history
    )
    assert LEAK_GIVE_UP in shown
    assert "<tool_calls>" not in shown and "<invoke" not in shown, "no markup fragment shown"


# What a provider left in the text after turning a DSML call into a real `git_push` call.
REMNANT = 'path" string="true">/workspace/backend-services-protocol'


def test_a_parameter_remnant_is_markup_and_never_streamed() -> None:
    """It showed as the step's intent in the cockpit and opened the Slack answer."""
    assert contains_leaked_calls(REMNANT) and strip_leaked_calls(REMNANT) == ""
    # The provider cuts the tag at different places.
    for cut in (
        '="path" string="true">/workspace/api',
        'name="path" string="true">/workspace/api',
        'parameter name="body" string="true">adds `relations.scope` to the model',
    ):
        assert contains_leaked_calls(cut) and strip_leaked_calls(cut) == "", cut
    assert not contains_leaked_calls('Set the path" string="true"> example in prose later')

    remnant_stream = LeakFilter()
    shown = "".join(
        remnant_stream.feed(piece) for piece in (REMNANT[:7], REMNANT[7:30], REMNANT[30:])
    )
    assert shown + remnant_stream.finish() == ""

    prose = "Let me check the CI logs before pushing the fix to the pull request branch now."
    prose_stream = LeakFilter()
    shown = "".join(prose_stream.feed(prose[i : i + 9]) for i in range(0, len(prose), 9))
    assert shown + prose_stream.finish() == prose
    short = LeakFilter()
    assert short.feed("Done.") + short.finish() == "Done."


async def test_a_remnant_beside_a_real_call_is_dropped_from_the_step() -> None:
    outcome = await run_turn(
        "org_remnant",
        steps=[("say_and_call", REMNANT, "list_dir", {"path": "."}), ("say", "All good.")],
    )

    stored = [m for r in _react_requests(outcome) for m in r.messages if m.role == "assistant"]
    assert stored and not any(REMNANT in (m.content or "") for m in stored)
    tokens = "".join(
        str(e.payload.get("delta") or "")
        for e in outcome.events
        if getattr(e, "event_type", None) and str(e.event_type).endswith("token")
    )
    assert REMNANT not in tokens
    assert outcome.session.conversation_history[-1].content == "All good."
