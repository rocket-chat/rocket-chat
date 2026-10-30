"""A spend limit that refuses a call ends the turn with a typed ``budget_blocked`` (P3.4)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from api.turns.failure import classify_turn_error

from specifications.interfaces.events import EventType
from specifications.interfaces.llm import (
    BudgetExceededError,
    CallRefusal,
    ModelExecutionError,
    StreamChunk,
)
from tests.turn_harness import ScriptedModel, run_turn

LIMIT = BudgetExceededError(
    "Spend limit reached (Agent, per day): $2.00 of $2.00.",
    scope="Agent, per day",
    limit_usd=2.0,
    spent_usd=2.0,
    limit_id="lim_day",
    subject="agent",
    period="day",
    resets_at=1_900_000_000,
)


class RefusingModel(ScriptedModel):
    """The gateway's answer when a spend limit has no room: an error chunk with its refusal."""

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        yield StreamChunk(
            is_finished=True,
            finish_reason="error",
            error=str(LIMIT),
            refusal=CallRefusal.from_error(LIMIT),
        )


async def test_a_refused_call_publishes_which_limit_and_when_it_resets() -> None:
    outcome = await run_turn("budget_turn", model=RefusingModel())
    blocked = [e for e in outcome.events if e.event_type == EventType.BUDGET_BLOCKED]
    assert len(blocked) == 1
    assert blocked[0].payload == {
        "limit_id": "lim_day",
        "subject": "agent",
        "period": "day",
        "spent": 2.0,
        "amount": 2.0,
        "resets_at": 1_900_000_000,
        "message": str(LIMIT),
    }
    errors = [e for e in outcome.events if e.event_type == EventType.ERROR]
    assert errors[-1].payload["error_kind"] == "budget"


def test_a_refusal_carried_by_the_model_error_is_classified() -> None:
    refusal = CallRefusal.from_error(LIMIT)
    assert classify_turn_error(ModelExecutionError("limit", refusal)) == "budget"
    credential = CallRefusal(
        kind="credential", message="withdrawn", reason="withdrawn", provider="anthropic"
    )
    assert classify_turn_error(ModelExecutionError("withdrawn", credential)) == "provider"
