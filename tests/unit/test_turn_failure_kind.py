"""A failed turn says what failed, so the cockpit offers the right way out."""

from __future__ import annotations

import pytest
from api.turns.failure import classify_turn_error
from sandbox_driver.exceptions import SandboxError

from specifications.interfaces.llm import BudgetExceededError, ModelExecutionError


@pytest.mark.parametrize(
    "error, kind",
    [
        (
            BudgetExceededError("monthly cap reached", scope="org", limit_usd=10, spent_usd=10),
            "budget",
        ),
        (SandboxError("container failed to start"), "sandbox"),
        (ModelExecutionError("AuthenticationError: invalid api key"), "provider"),
        (ModelExecutionError("503 service unavailable"), "provider"),
        (ModelExecutionError("model not found: acme/unknown"), "model"),
        (RuntimeError("Unauthorized: invalid api key"), "provider"),
        (KeyError("session"), "internal"),
    ],
)
def test_each_failure_has_its_kind(error: BaseException, kind: str) -> None:
    assert classify_turn_error(error) == kind
