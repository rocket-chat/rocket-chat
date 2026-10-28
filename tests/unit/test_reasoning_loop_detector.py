"""Unit tests for reasoning loop detection and sanitization."""

from __future__ import annotations

import pytest
from agent_core.reasoning_guard import (
    DECIDE_NOW_NOTE,
    decisive_request,
    detect_repetition_loop,
    sanitize_partial_reasoning,
)
from api.services.settings import save_org_config
from llm_gateway.reasoning import sanitize_replayed_reasoning

from specifications.interfaces.llm import ChatMessage, ModelRequest
from tests.turn_harness import run_turn


def test_detect_repetition_loop_ignores_short_or_varied_reasoning() -> None:
    assert not detect_repetition_loop("Short thought")
    varied = " ".join(
        f"Step {i}: inspecting file_{i}.py to confirm implementation." for i in range(50)
    )
    assert not detect_repetition_loop(varied)


def test_detect_repetition_loop_catches_autoregressive_attractor_loops() -> None:
    stutter = "OK. Writing. Let me go. OK. Let me write. Go. Writing. OK. Let me produce. Go. " * 8
    assert detect_repetition_loop("Pre-analysis here. " + stutter)

    short_cycle = "Wait, let me see. " * 10
    assert detect_repetition_loop(short_cycle)

    single_word_cycle = "thinking " * 15
    assert detect_repetition_loop(single_word_cycle)


def test_sanitize_partial_reasoning_strips_degenerate_loops() -> None:
    stutter = "OK. Writing. Let me go. " * 15
    clean_prefix = "The root cause is a bad configuration in nats.yaml."
    full = f"{clean_prefix}\n{stutter}"

    sanitized = sanitize_partial_reasoning(full)
    assert clean_prefix in sanitized
    assert "OK. Writing. Let me go. OK. Writing. Let me go." not in sanitized


def test_decisive_request_cleans_looping_notes() -> None:
    request = ModelRequest(
        model="openrouter/deepseek/deepseek-v4.1-flash",
        messages=[ChatMessage(role="user", content="fix the bug")],
    )
    stutter = "OK. Writing. Let me go. " * 20
    retry = decisive_request(request, openrouter=True, partial_reasoning=stutter)

    user_note = str(retry.messages[-1].content)
    assert DECIDE_NOW_NOTE in user_note
    # The pure stutter must not be re-injected as the partial analysis
    assert "Your partial analysis before stopping was:" not in user_note


def test_sanitize_replayed_reasoning_strips_trailing_repetition() -> None:
    stutter = "Now let me check. " * 12
    reasoning = "Good analysis of system logs.\n" + stutter
    cleaned = sanitize_replayed_reasoning(reasoning)

    assert "Good analysis of system logs." in cleaned
    assert "Now let me check. Now let me check. Now let me check." not in cleaned


@pytest.mark.setting("models.reasoning_step_limit_tokens")
async def test_repetition_loop_triggers_decisive_retry_turn() -> None:
    org = "repetition_loop_retry_org"
    # Even with a high token limit (e.g. 16,000 tokens), repetition must trigger decisive retry
    await save_org_config(None, org, "models", {"reasoning_step_limit_tokens": 16000}, [])

    loop_reasoning = "OK. Writing. Let me go. OK. Let me write. Go. " * 10
    outcome = await run_turn(
        org,
        steps=[
            ("think_and_call", loop_reasoning, "bash", {"command": "ls"}),
            ("say", "Action taken after loop was stopped."),
        ],
    )

    first, second = outcome.model.requests[:2]
    assert DECIDE_NOW_NOTE not in str(first.messages[-1].content)
    assert DECIDE_NOW_NOTE in str(second.messages[-1].content)
    assert "Action taken after loop was stopped." in outcome.text_of_events()
