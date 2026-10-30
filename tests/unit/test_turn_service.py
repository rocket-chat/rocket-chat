"""Shared TurnService.

Both transports resolve how a turn runs through one service, so the agent persona, model,
turn limit and tool scope cannot drift between the web cockpit and Slack.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from api.services.turn_service import TurnService, _resolve_model_generation

from specifications.interfaces.agent import AgentEvent, SystemPrompt


class _Agent:
    id = "persona-x"
    name = "Agent X"
    system_prompt = "PERSONA"
    whitelisted_tools: ClassVar[list[str]] = ["file_read"]
    model = "agent-model"
    max_turns = 7
    enabled = True


async def _resolve_agent(db: Any, org: str, user: str, agent_id: str | None) -> _Agent:
    return _Agent()


async def _resolve_routing(**kwargs: Any) -> tuple[str, str, str]:
    return ("ROUTER", "effective-model", "reason-x")


def _service() -> TurnService:
    return TurnService(
        resolve_agent=_resolve_agent,
        resolve_routing=_resolve_routing,
    )


@pytest.mark.asyncio
async def test_plan_turn_resolves_persona_model_and_scope() -> None:
    plan = await _service().plan_turn(
        db_mgr=None,
        org_id="org",
        user_id="user",
        agent_id="persona-x",
        enabled_toolsets=frozenset({"slack"}),
    )

    assert plan.id == "persona-x"
    assert plan.system_prompt == "PERSONA"
    assert plan.whitelisted_tools == ["file_read"]
    assert plan.model == "effective-model"
    assert plan.max_turns == 7, "the agent's own limit, under the organization's (100)"
    assert plan.model_router == "ROUTER"
    assert plan.routing_reason == "reason-x"
    assert plan.enabled_toolsets == frozenset({"slack"})


class _Orchestrator:
    default_model = "platform-default"

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    async def process_user_turn(self, session_id: str, prompt: str, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        yield AgentEvent(event_type="turn_complete", session_id=session_id, payload={})


@pytest.mark.asyncio
async def test_stream_turn_forwards_the_plan_to_the_orchestrator() -> None:
    service = _service()
    plan = await service.plan_turn(db_mgr=None, org_id="org", user_id="user", agent_id="persona-x")
    orchestrator = _Orchestrator()

    events = [
        event
        async for event in service.stream_turn(
            orchestrator,  # type: ignore[arg-type]
            plan,
            "session-1",
            "hello",
            system_prompt_parts=SystemPrompt(stable="stable"),
            tenant_org_id="org",
            tenant_user_id="user",
        )
    ]

    assert [e.event_type for e in events] == ["turn_complete"]
    assert orchestrator.kwargs is not None
    assert orchestrator.kwargs["model"] == plan.model
    assert orchestrator.kwargs["max_turns"] == plan.max_turns
    assert orchestrator.kwargs["model_router"] == plan.model_router
    assert orchestrator.kwargs["whitelisted_tools"] == plan.whitelisted_tools


def test_resolve_generation_params_aligns_claude_thinking_budget_with_step_limit() -> None:
    models_config = {
        "temperature": 0.2,
        "reasoning_step_limit_tokens": 8000,
        "default_reasoning_effort": None,
    }
    params = _resolve_model_generation(
        model="anthropic/claude-3-7-sonnet",
        agent=None,
        models=models_config,
        fallbacks=(),
    )
    # Default Claude budget should align to step limit (8000), not exceed it at 16000
    assert params.thinking_budget_tokens == 8000
    assert params.reasoning_step_limit_tokens == 8000 + 2048


def test_the_effort_chosen_for_the_turn_reaches_the_generation_params() -> None:
    # A turn's (or session's) effort is a preset the resolver has already applied to
    # ``models.default_reasoning_effort``; the agent's thinking budget follows the level.
    models_config = {
        "temperature": 0.2,
        "reasoning_step_limit_tokens": 8000,
        "default_reasoning_effort": "high",
    }
    params = _resolve_model_generation(
        model="openai/o3-mini", agent=None, models=models_config, fallbacks=()
    )
    assert params.reasoning_effort == "high"


@pytest.mark.asyncio
async def test_an_agent_given_slack_tools_gets_the_slack_toolset_in_any_session() -> None:
    from api.services.turn_service import toolsets_listed

    class _SlackAgent(_Agent):
        whitelisted_tools: ClassVar[list[str]] = ["file_read", "slack_send_message"]

    async def resolve(db: Any, org: str, user: str, agent_id: str | None) -> _Agent:
        return _SlackAgent()

    service = TurnService(resolve_agent=resolve, resolve_routing=_resolve_routing)
    web = await service.plan_turn(db_mgr=None, org_id="org", user_id="user", agent_id="x")
    plain = await _service().plan_turn(db_mgr=None, org_id="org", user_id="user", agent_id="x")

    assert web.enabled_toolsets == frozenset({"slack"})
    assert plain.enabled_toolsets == frozenset()
    assert toolsets_listed(None) == frozenset()
