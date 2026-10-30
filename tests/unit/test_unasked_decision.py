"""An answer that ends on a choice for the person is sent back once to ask it with ask_question."""

from __future__ import annotations

from agent_core.decisions import leaves_decision_unasked, unasked_decision_notice

from tests.turn_harness import TurnOutcome, run_turn

BLOCKED_REPORT = """I've stopped before finishing. The protocol commits are in; the services are not.

**Blocker:** the sandbox can't build either service: `go.mod` pins a revision that is gone.

**Decision needed from you:** how should I proceed with the build blocker?
1. Ask the owners to fix the pin on master, then I resume (recommended).
2. Point the `replace` at a reachable revision on the branch only.
3. Push the branches and let CI compile them.
"""


def test_a_decision_with_listed_options_at_the_end_is_unasked() -> None:
    assert leaves_decision_unasked(BLOCKED_REPORT)
    lettered = "Which option do you prefer?\nA) Keep the field\nB) Rename it"
    assert leaves_decision_unasked(lettered)
    notice = unasked_decision_notice(BLOCKED_REPORT)
    assert notice is not None and "`ask_question`" in notice


def test_offers_settled_choices_and_plain_lists_are_not_decisions() -> None:
    offer = "Done: the PR is green.\n1. Added the field\n2. Wired it\n\nWant me to also add docs?"
    settled = "Decision needed: A or B?\n1. A\n2. B\n" + "I picked A and finished. " * 200
    lone_option = "How should I proceed?\n1. Retry later"
    for answer in (offer, settled, lone_option, ""):
        assert not leaves_decision_unasked(answer), answer[:40]


def _reminders(outcome: TurnOutcome) -> int:
    return sum(
        "Ask it with `ask_question`" in outcome.request_notes(i)
        for i in range(len(outcome.model.requests))
    )


async def test_the_turn_reopens_once_to_ask_the_decision() -> None:
    outcome = await run_turn(
        "unasked_decision",
        steps=[("say", BLOCKED_REPORT), ("say", BLOCKED_REPORT), ("say", "unreached")],
    )

    assert _reminders(outcome) == 1
    # Once per turn: the same answer given again ends it.
    assert outcome.session.conversation_history[-1].content == BLOCKED_REPORT.strip()


async def test_a_finished_answer_ends_the_turn_at_once() -> None:
    outcome = await run_turn("decided", steps=[("say", "Done. Want me to open the PR?")])

    assert _reminders(outcome) == 0
    assert outcome.session.conversation_history[-1].content == "Done. Want me to open the PR?"
