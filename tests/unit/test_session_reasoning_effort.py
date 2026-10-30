"""The reasoning effort chosen for a session reaches the model on every turn of that session.

The composer saves the choice on the session (metadata ``reasoning_effort``); a turn that names
none of its own runs with it. A value the contract does not know is ignored, not sent.
"""

from __future__ import annotations

from turn_harness import TurnOutcome, run_turn

from specifications.interfaces.session import (
    SESSION_REASONING_EFFORTS,
    session_reasoning_effort,
)


def _efforts(outcome: TurnOutcome) -> set[str | None]:
    return {r.reasoning_effort for r in outcome.model.requests if r.tools}


async def test_the_session_effort_is_sent_with_each_model_call() -> None:
    outcome = await run_turn("effort_low", metadata={"reasoning_effort": "low"})

    assert _efforts(outcome) == {"low"}


async def test_without_a_session_effort_the_turn_keeps_its_default() -> None:
    chosen = await run_turn("effort_high", metadata={"reasoning_effort": "high"})
    default = await run_turn("effort_default")

    assert _efforts(chosen) == {"high"}
    assert "high" not in _efforts(default)


async def test_an_unknown_effort_on_the_session_is_ignored() -> None:
    tampered = await run_turn("effort_bad", metadata={"reasoning_effort": "ludicrous"})
    default = await run_turn("effort_plain")

    assert _efforts(tampered) == _efforts(default)


def test_the_contract_reads_only_known_efforts() -> None:
    assert session_reasoning_effort({"reasoning_effort": "minimal"}) == "minimal"
    assert session_reasoning_effort({"reasoning_effort": "max"}) is None
    assert session_reasoning_effort({"reasoning_effort": 3}) is None
    assert session_reasoning_effort(None) is None
    assert "none" in SESSION_REASONING_EFFORTS
