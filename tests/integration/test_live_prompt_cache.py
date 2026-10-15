"""Against a real provider, the second step of a turn reads the history from the prompt cache.

Opt-in (it spends provider credit): ``RUN_LIVE_LLM_TESTS=1`` with a key for the model in
``ROCKET_LIVE_CACHE_MODEL`` (default an Anthropic-style model through OpenRouter, the family
that needs explicit cache markers). Providers only cache prefixes above a minimum size, so the
stable prompt here is deliberately long.
"""

from __future__ import annotations

import os

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from llm_gateway.gateway import LiteLLMGateway
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import SystemPrompt

MODEL = os.getenv("ROCKET_LIVE_CACHE_MODEL", "openrouter/anthropic/claude-haiku-4.5")

pytestmark = pytest.mark.skipif(
    not (os.getenv("RUN_LIVE_LLM_TESTS") and os.getenv("OPENROUTER_API_KEY")),
    reason="set RUN_LIVE_LLM_TESTS=1 and OPENROUTER_API_KEY (spends provider credit)",
)


async def test_the_second_step_reads_the_cached_prefix() -> None:
    orchestrator = AsyncReActOrchestrator(
        LiteLLMGateway(), ToolRegistry(driver=FakeSandboxDriver(), session_id="live_cache")
    )
    stable = "You are a careful engineer. " + " ".join(f"Rule {n}: be precise." for n in range(900))
    usage = []
    async for event in orchestrator.process_user_turn(
        "live_cache",
        "List the workspace with list_dir, then say how many entries it has.",
        model=MODEL,
        system_prompt_parts=SystemPrompt(stable=stable, dynamic="Active model: live test."),
        max_turns=4,
    ):
        if event.event_type == "model_usage":
            usage.append(event.payload)

    assert len(usage) >= 2, "a tool step and an answer step"
    assert usage[1]["cache_read_tokens"] > 0, usage
