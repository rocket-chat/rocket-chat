"""What a session cost: tokens and dollars in total, per turn and per model.

The usage ledger records every model call the moment it is made, so these numbers are live while a
turn runs; a turn's own trace only gets its token totals when the turn ends. A call belongs to the
turn that started last before it, so the calls a turn's end triggers (the title, the summary, the
suggested next prompt) are counted with it.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


class UsageTotals(BaseModel):
    calls: int = Field(default=0, description="Model calls")
    prompt_tokens: int = Field(default=0, description="Tokens sent, cached ones included")
    cache_read_tokens: int = Field(
        default=0, description="Of the prompt tokens, those served from the provider's cache"
    )
    cache_creation_tokens: int = Field(default=0, description="Tokens written to the cache")
    completion_tokens: int = Field(default=0, description="Tokens generated, reasoning included")
    total_tokens: int = Field(default=0, description="Prompt and completion tokens")
    cost_usd: float = Field(default=0.0, description="Cost billed by the provider, in dollars")
    own_key_cost_usd: float = Field(
        default=0.0, description="Of the cost, what a member's own provider key paid"
    )


class TurnUsage(UsageTotals):
    turn_index: int = Field(..., description="1-based turn number within the session")
    started_at: int = Field(..., description="Turn start, epoch seconds")
    ended_at: int | None = Field(default=None, description="Turn end; null while it runs")
    status: str = Field(..., description="running, completed, error or interrupted")


class ModelUsage(UsageTotals):
    model: str = Field(..., description="The model the calls went to")


class SessionUsage(BaseModel):
    session_id: str = Field(..., description="The session")
    totals: UsageTotals = Field(..., description="Everything the session has used")
    turns: list[TurnUsage] = Field(
        default_factory=list, description="Per turn, oldest first; empty without turn traces"
    )
    models: list[ModelUsage] = Field(default_factory=list, description="Per model, dearest first")
    before_first_turn: UsageTotals = Field(
        default_factory=UsageTotals,
        description="Calls made before the first recorded turn started",
    )


@dataclass(frozen=True)
class TurnWindow:
    turn_index: int
    started_at: int
    ended_at: int | None
    status: str


def _add(totals: UsageTotals, event: dict[str, Any]) -> None:
    totals.calls += 1
    totals.prompt_tokens += event["prompt_tokens"]
    totals.cache_read_tokens += event["cache_read_tokens"]
    totals.cache_creation_tokens += event["cache_creation_tokens"]
    totals.completion_tokens += event["completion_tokens"]
    totals.total_tokens += event["prompt_tokens"] + event["completion_tokens"]
    totals.cost_usd += event["cost_usd"]
    if event["key_source"] == "user":
        totals.own_key_cost_usd += event["cost_usd"]


def _rounded[T: UsageTotals](totals: T) -> T:
    totals.cost_usd = round(totals.cost_usd, 6)
    totals.own_key_cost_usd = round(totals.own_key_cost_usd, 6)
    return totals


def summarize_session_usage(
    session_id: str, events: Iterable[dict[str, Any]], turns: Sequence[TurnWindow]
) -> SessionUsage:
    """Total ``events`` (ledger rows of one session), by turn and by model."""
    ordered = sorted(turns, key=lambda t: (t.started_at, t.turn_index))
    starts = [t.started_at for t in ordered]
    totals = UsageTotals()
    before = UsageTotals()
    per_turn = [
        TurnUsage(
            turn_index=t.turn_index, started_at=t.started_at, ended_at=t.ended_at, status=t.status
        )
        for t in ordered
    ]
    per_model: dict[str, ModelUsage] = {}
    for event in events:
        _add(totals, event)
        _add(per_model.setdefault(event["model"], ModelUsage(model=event["model"])), event)
        position = bisect_right(starts, event["created_at"]) - 1
        _add(per_turn[position] if position >= 0 else before, event)
    return SessionUsage(
        session_id=session_id,
        totals=_rounded(totals),
        turns=[_rounded(t) for t in per_turn],
        models=sorted((_rounded(m) for m in per_model.values()), key=lambda m: -m.cost_usd),
        before_first_turn=_rounded(before),
    )
