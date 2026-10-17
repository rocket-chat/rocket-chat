"""Shared TurnService.

Both transports resolve how a turn runs through one service, so the agent persona, model,
turn limit and tool scope cannot drift between the web cockpit and Slack.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from api.services.turn_service import TurnService

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


async def _resolve_max_turns(**kwargs: Any) -> int:
    return 9


def _service() -> TurnService:
    return TurnService(
        resolve_agent=_resolve_agent,
        resolve_routing=_resolve_routing,
        resolve_max_turns=_resolve_max_turns,
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
    assert plan.max_turns == 9
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
