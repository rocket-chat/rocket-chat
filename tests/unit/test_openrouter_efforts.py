"""A reasoning effort is mapped onto one the OpenRouter model takes, and ``none`` turns it off."""

from __future__ import annotations

from typing import Any

import pytest
from api.services import openrouter_efforts
from api.services.openrouter_efforts import (
    OpenRouterEfforts,
    ReasoningSupport,
    map_effort,
    parse_catalogue,
)
from api.services.settings import save_org_config
from api.turns.shared import _turn_service

DEEPSEEK = ReasoningSupport(("max", "high", "low", "none"), "high")
DEEPSEEK_ID = "openrouter/deepseek/deepseek-v4-pro-0813"


def test_a_supported_effort_is_kept_and_an_unsupported_one_rounds_down_on_a_tie() -> None:
    assert map_effort("high", DEEPSEEK) == "high"
    assert map_effort("medium", DEEPSEEK) == "low"
    assert map_effort("xhigh", DEEPSEEK) == "high"
    assert map_effort("minimal", DEEPSEEK) == "low", "never none for a request that wanted some"
    assert map_effort("none", DEEPSEEK) == "none"


def test_an_effort_is_left_alone_when_the_model_is_unknown() -> None:
    assert map_effort("medium", None) == "medium"
    assert map_effort("medium", ReasoningSupport(())) == "medium"


def test_none_falls_to_the_lowest_level_when_the_model_cannot_switch_reasoning_off() -> None:
    assert map_effort("none", ReasoningSupport(("high", "xhigh"))) == "high"


def test_the_catalogue_is_read_into_efforts_per_model() -> None:
    parsed = parse_catalogue(
        [
            {
                "id": "a/b",
                "reasoning": {"supported_efforts": ["high", "none"], "default_effort": "high"},
            },
            {"id": "c/d", "reasoning": {"supported_efforts": []}},
            {"id": "e/f"},
        ]
    )

    assert parsed == {"openrouter/a/b": ReasoningSupport(("high", "none"), "high")}


async def test_the_catalogue_is_fetched_once_and_a_failure_is_not_retried_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fetch() -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return [{"id": "x/y", "reasoning": {"supported_efforts": ["low"]}}]

    monkeypatch.setattr(openrouter_efforts, "_fetch_catalogue", fetch)
    source = OpenRouterEfforts()

    assert await source.support_for("openrouter/x/y") == ReasoningSupport(("low",))
    assert await source.support_for("openrouter/x/y") is not None
    assert await source.support_for("anthropic/claude") is None
    assert calls == 1


async def test_a_failed_fetch_leaves_the_effort_as_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    calls = 0

    async def fetch() -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(openrouter_efforts, "_fetch_catalogue", fetch)
    source = OpenRouterEfforts()

    assert await source.support_for(DEEPSEEK_ID) is None
    assert await source.support_for(DEEPSEEK_ID) is None
    assert calls == 1


@pytest.fixture
def deepseek_efforts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def support_for(model: str) -> ReasoningSupport | None:
        return DEEPSEEK if model == DEEPSEEK_ID else None

    monkeypatch.setattr(openrouter_efforts.openrouter_efforts, "support_for", support_for)


async def _generation(org: str, model: str) -> Any:
    plan = await _turn_service.plan_turn(
        db_mgr=None,
        org_id=org,
        user_id="u1",
        agent_id=None,
        global_default_model=model,
    )
    return plan.policy.generation


@pytest.mark.setting("models.default_reasoning_effort")
@pytest.mark.usefixtures("deepseek_efforts")
async def test_the_default_effort_is_mapped_onto_what_the_openrouter_model_takes() -> None:
    await save_org_config(None, "effort_map", "models", {"default_reasoning_effort": "medium"}, [])

    assert (await _generation("effort_map", DEEPSEEK_ID)).reasoning_effort == "low"


@pytest.mark.setting("models.default_reasoning_effort")
@pytest.mark.usefixtures("deepseek_efforts")
async def test_none_reaches_an_openrouter_model_and_is_dropped_for_any_other_provider() -> None:
    await save_org_config(None, "effort_off", "models", {"default_reasoning_effort": "none"}, [])

    assert (await _generation("effort_off", DEEPSEEK_ID)).reasoning_effort == "none"
    other = await _generation("effort_off", "openai/o3-mini")
    assert other.reasoning_effort is None
    assert other.reasoning is None, "the reasoning default must not turn it back on"
    assert other.thinking_budget_tokens is None
