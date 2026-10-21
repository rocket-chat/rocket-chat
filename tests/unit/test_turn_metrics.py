"""A finished turn's trace says how it spent its time and context, measured in a real turn."""

from __future__ import annotations

from typing import Any

import pytest
from api.db.store import TurnTraceRecord
from api.turns import finalise

from tests.turn_harness import run_turn

REASONING = "Checking which file holds the handler. " * 20


async def test_a_real_turn_records_its_steps_reasoning_and_fixed_overhead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[TurnTraceRecord] = []

    async def keep(_db: Any, trace: TurnTraceRecord) -> None:
        saved.append(trace)

    monkeypatch.setattr(finalise, "persist_turn_trace", keep)

    await run_turn(
        "turn_metrics",
        steps=[("think_and_call", REASONING, "bash", {"command": "ls"}), ("say", "Done.")],
    )

    metrics = saved[-1].tokens["metrics"]
    assert metrics["steps"] == 2
    assert metrics["reasoning_chars"] == len(REASONING) == metrics["max_step_reasoning_chars"]
    assert metrics["tool_calls"] == 1 and metrics["model_ms"] >= 0
    # The system prompt and the tool definitions are counted, not the conversation.
    assert metrics["overhead_tokens"] > 1000
