"""Unit tests for Max Turns overrides (Agent, Org, Env) and Post-Failure Synthesis."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator, OrchestratorState
from agent_core.tools.registry import ToolRegistry
from api.models import AgentPersonaModel
from api.services.settings import reset_memory_settings, save_org_config
from api.turns.routing import _resolve_effective_max_turns
from config_engine.cascading import CascadingSettingsResolver

from specifications.interfaces.agent import DEFAULT_MAX_TURNS, AgentEvent
from specifications.interfaces.llm import StreamChunk


@pytest.mark.asyncio
async def test_default_max_turns_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = MagicMock()
    registry = MagicMock(spec=ToolRegistry)
    registry.bind_subagent_runner = MagicMock()
    registry.get_tool_definitions = MagicMock(return_value=[])

    # Default is DEFAULT_MAX_TURNS
    monkeypatch.delenv("AGENT_MAX_TURNS", raising=False)
    monkeypatch.delenv("ROCKET_MAX_TURNS", raising=False)
    monkeypatch.delenv("MAX_TURNS", raising=False)
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)
    assert orchestrator.max_turns == DEFAULT_MAX_TURNS

    # Env var override
    monkeypatch.setenv("AGENT_MAX_TURNS", "45")
    orchestrator_env = AsyncReActOrchestrator(gateway=gateway, registry=registry)
    assert orchestrator_env.max_turns == 45


@pytest.mark.asyncio
async def test_cascading_settings_max_turns_default() -> None:
    resolver = CascadingSettingsResolver()
    gen_defaults = resolver.get_system_defaults("general")
    assert gen_defaults.get("max_turns") == DEFAULT_MAX_TURNS

    # The turn limit lives in one domain only; `models` no longer carries a duplicate.
    assert "max_turns" not in resolver.get_system_defaults("models")


@pytest.mark.asyncio
async def test_resolve_effective_max_turns_cascading() -> None:
    org_id = "test_org_turns"

    # 1. Default fallback is DEFAULT_MAX_TURNS
    res_default = await _resolve_effective_max_turns(agent=None, org_id=org_id)
    assert res_default == DEFAULT_MAX_TURNS

    # 2. Org settings override
    await save_org_config(None, org_id, "general", {"max_turns": 40}, [])
    res_org = await _resolve_effective_max_turns(agent=None, org_id=org_id)
    assert res_org == 40

    # 3. Agent persona override takes precedence over org settings
    custom_agent = AgentPersonaModel(
        id="persona-custom",
        name="Custom Agent",
        role_title="Specialist",
        system_prompt="Custom prompt",
        max_turns=12,
    )
    res_agent = await _resolve_effective_max_turns(agent=custom_agent, org_id=org_id)
    assert res_agent == 12

    # 4. The organization's limit is a ceiling: lowering it later caps existing agents
    await save_org_config(None, org_id, "general", {"max_turns": 8}, [])
    assert await _resolve_effective_max_turns(agent=custom_agent, org_id=org_id) == 8

    # Cleanup
    reset_memory_settings()


@pytest.mark.asyncio
async def test_post_failure_synthesis_on_turn_budget_exhaustion() -> None:
    """When the turn budget is reached while tools are still called, orchestrator triggers synthesis."""
    gateway = MagicMock()
    registry = MagicMock(spec=ToolRegistry)
    registry.bind_subagent_runner = MagicMock()
    registry.get_tool_definitions = MagicMock(return_value=[])
    registry.call_tool = AsyncMock(return_value="tool output")

    # Mock chat_stream so turns 1 and 2 call tools, and turn 3 (synthesis) returns text
    call_count = 0

    async def mock_chat_stream(request: Any):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            # Model calls a tool
            yield StreamChunk(text_delta="Let me check.")
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call_{call_count}",
                    "function": {"name": "test_tool", "arguments": '{"param": 1}'},
                }
            )
            yield StreamChunk(is_finished=True)
        else:
            # Synthesis turn with tools disabled
            assert len(request.tools) == 0  # Tools must be disabled during synthesis!
            yield StreamChunk(text_delta="Here is the final synthesized answer.")
            yield StreamChunk(is_finished=True)

    gateway.chat_stream = mock_chat_stream

    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry, max_turns=2)
    session_id = "test_synthesis_sess"

    events: list[AgentEvent] = []
    async for ev in orchestrator.process_user_turn(
        session_id=session_id,
        user_message="Investigate why this happened",
        max_turns=2,
    ):
        events.append(ev)

    event_types = [e.event_type for e in events]
    assert "post_failure_synthesis" in event_types

    synthesis_event = next(e for e in events if e.event_type == "post_failure_synthesis")
    assert synthesis_event.payload["turns_taken"] == 2
    assert "Maximum turn limit reached" in synthesis_event.payload["reason"]

    # History should contain the synthesis answer
    history = orchestrator._session_histories[session_id]
    assistant_msgs = [m for m in history if m.role == "assistant"]
    assert any("Here is the final synthesized answer" in (m.content or "") for m in assistant_msgs)
    assert orchestrator.get_session_state(session_id) == OrchestratorState.COMPLETED.value
