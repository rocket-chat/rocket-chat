"""A step that reasons past the limit without acting is asked again, once, to decide."""

from __future__ import annotations

from typing import Any

import pytest
from agent_core.reasoning_guard import DECIDE_NOW_NOTE, decisive_request
from api.services.settings import save_org_config

from specifications.interfaces.llm import ChatMessage, ModelRequest
from tests.turn_harness import ScriptedModel, run_turn

RAMBLING = "Weighing the options again. " * 100  # about 700 estimated tokens


@pytest.mark.setting("models.reasoning_step_limit_tokens")
async def test_a_step_that_reasons_past_the_limit_is_asked_again_to_decide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    billed: list[dict[str, Any]] = []

    async def record_usage(self: ScriptedModel, *args: Any, **kwargs: Any) -> None:
        billed.append(kwargs)

    monkeypatch.setattr(ScriptedModel, "record_usage", record_usage)
    org = "wire_reasoning_limit"
    await save_org_config(None, org, "models", {"reasoning_step_limit_tokens": 100}, [])

    outcome = await run_turn(
        org,
        steps=[("think_and_call", RAMBLING, "bash", {"command": "ls"}), ("say", "Decided.")],
    )

    first, second = outcome.model.requests[:2]
    assert DECIDE_NOW_NOTE not in str(first.messages[-1].content)
    assert DECIDE_NOW_NOTE in str(second.messages[-1].content)
    assert second.reasoning_effort == "none" and second.thinking_budget_tokens is None
    assert len(billed) == 2 and billed[0]["completion_tokens"] > 500, "the aborted call is billed"
    assert "Decided." in outcome.text_of_events()


@pytest.mark.setting("models.reasoning_step_limit_tokens")
async def test_a_limit_of_zero_lets_a_step_reason_as_long_as_it_wants() -> None:
    org = "wire_reasoning_limit_off"
    await save_org_config(None, org, "models", {"reasoning_step_limit_tokens": 0}, [])

    outcome = await run_turn(
        org,
        steps=[("think_and_call", RAMBLING, "bash", {"command": "ls"}), ("say", "Done.")],
    )

    assert all(DECIDE_NOW_NOTE not in str(r.messages[-1].content) for r in outcome.model.requests)


@pytest.mark.setting("models.reasoning_step_limit_tokens")
async def test_a_step_that_acts_before_the_limit_is_not_interrupted() -> None:
    org = "wire_reasoning_limit_short"
    await save_org_config(None, org, "models", {"reasoning_step_limit_tokens": 5000}, [])

    outcome = await run_turn(
        org,
        steps=[("think_and_call", RAMBLING, "bash", {"command": "ls"}), ("say", "Done.")],
    )

    assert all(DECIDE_NOW_NOTE not in str(r.messages[-1].content) for r in outcome.model.requests)


def test_the_retry_turns_reasoning_off_the_way_each_provider_takes_it() -> None:
    request = ModelRequest(
        model="m",
        messages=[ChatMessage(role="user", content="go")],
        reasoning_effort="high",
        thinking_budget_tokens=4000,
    )

    on_openrouter = decisive_request(request, openrouter=True)
    elsewhere = decisive_request(request, openrouter=False)

    assert on_openrouter.reasoning_effort == "none"
    assert elsewhere.reasoning_effort is None
    assert on_openrouter.thinking_budget_tokens is None
    assert request.messages == [ChatMessage(role="user", content="go")], "the original is kept"
