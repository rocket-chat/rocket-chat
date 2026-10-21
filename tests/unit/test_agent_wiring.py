"""Every field of an agent persona and of a subagent role changes what a turn does."""

from __future__ import annotations

from typing import Any

from api.models import AgentPersonaModel, SubagentConfigModel
from api.services.personas import save_persona
from api.services.subagents import save_subagent_config
from turn_harness import TurnOutcome, run_turn

ORG = "wire_agents"


async def _run_with_persona(org: str, **fields: Any) -> TurnOutcome:
    persona = AgentPersonaModel(
        id=f"persona-{org}",
        name="Reviewer",
        role_title="Code reviewer",
        system_prompt=fields.pop("system_prompt", "You are the REVIEWER-PERSONA-MARKER."),
        scope="org",
        **fields,
    )
    saved = await save_persona(None, org, "admin", True, persona)
    return await run_turn(org, metadata={"agent_id": saved.id})


def _first_agent_request(outcome: TurnOutcome) -> Any:
    return next(r for r in outcome.model.requests if r.tools)


async def test_the_personas_system_prompt_and_model_drive_the_turn() -> None:
    outcome = await _run_with_persona("wire_persona_prompt", model="persona/model-x")

    request = _first_agent_request(outcome)
    system = "\n".join(m.content or "" for m in request.messages if m.role == "system")
    assert "REVIEWER-PERSONA-MARKER" in system
    assert request.model == "persona/model-x"


async def test_the_personas_generation_settings_reach_the_model_request() -> None:
    outcome = await _run_with_persona(
        "wire_persona_generation",
        temperature=0.35,
        thinking_budget_tokens=2048,
        reasoning_effort="high",
    )

    request = _first_agent_request(outcome)
    assert request.temperature == 0.35
    assert request.thinking_budget_tokens == 2048
    assert request.reasoning_effort == "high"


async def test_the_personas_tool_whitelist_limits_the_tools_offered() -> None:
    outcome = await _run_with_persona(
        "wire_persona_tools", whitelisted_tools=["file_read", "think"]
    )

    request = _first_agent_request(outcome)
    # file_read is core; think is allowed but deferred (listed, loaded on demand).
    assert {tool.name for tool in request.tools} == {"file_read", "load_tools", "search_tools"}
    assert "think" in (request.messages[0].content or "")
    assert "bash_exec" not in (request.messages[0].content or "").split("[Tools to load")[-1]


async def test_the_personas_turn_limit_bounds_the_turn() -> None:
    from api.services.settings import save_org_config
    from api.turns.shared import _turn_service

    org = "wire_persona_turns"
    await save_org_config(None, org, "general", {"max_turns": 50}, [])
    persona = AgentPersonaModel(
        id="persona-turns",
        name="Quick",
        role_title="r",
        system_prompt="s",
        max_turns=3,
        scope="org",
    )
    saved = await save_persona(None, org, "admin", True, persona)

    plan = await _turn_service.plan_turn(
        db_mgr=None, org_id=org, user_id="u1", agent_id=saved.id, global_default_model="p/m"
    )

    assert plan.max_turns == 3


async def test_the_personas_sandbox_choices_and_attachments_reach_the_binding() -> None:
    from api.turns.shared import _turn_service

    org = "wire_persona_binding"
    persona = AgentPersonaModel(
        id="persona-binding",
        name="Builder",
        role_title="r",
        system_prompt="s",
        sandbox_image="registry.example/builder:1",
        cpu_limit="1.5",
        memory_limit="3Gi",
        skill_ids=["skill-a"],
        knowledge_base_ids=["kb-a"],
        scope="org",
    )
    saved = await save_persona(None, org, "admin", True, persona)

    plan = await _turn_service.plan_turn(
        db_mgr=None, org_id=org, user_id="u1", agent_id=saved.id, global_default_model="p/m"
    )

    assert (plan.sandbox_image, plan.cpu_limit, plan.memory_limit) == (
        "registry.example/builder:1",
        "1.5",
        "3Gi",
    )
    assert (plan.skill_ids, plan.knowledge_base_ids) == (("skill-a",), ("kb-a",))


# --- subagent roles ---------------------------------------------------------------------


async def _delegate(org: str, **role: Any) -> TurnOutcome:
    config = SubagentConfigModel(
        id="auditor",
        name="Auditor",
        role_title="Auditor",
        description="Audits things",
        system_prompt=role.pop("system_prompt", "You are the AUDITOR-ROLE-MARKER."),
        **role,
    )
    await save_subagent_config(None, org, config)
    return await run_turn(
        org, script=[("delegate_subagent", {"role": "auditor", "task": "Audit the repo"})]
    )


def _subagent_request(outcome: TurnOutcome) -> Any:
    return next(
        r
        for r in outcome.model.requests
        if any("AUDITOR-ROLE-MARKER" in (m.content or "") for m in r.messages)
    )


async def test_a_roles_prompt_model_and_temperature_drive_the_delegate() -> None:
    outcome = await _delegate("wire_sub_prompt", model="sub/model-y", temperature=0.9)

    request = _subagent_request(outcome)
    assert request.model == "sub/model-y" and request.temperature == 0.9


async def test_a_roles_tool_whitelist_limits_what_the_delegate_is_offered() -> None:
    outcome = await _delegate("wire_sub_tools", whitelisted_tools=["file_read"])

    assert {tool.name for tool in _subagent_request(outcome).tools} == {"file_read"}


async def test_a_disabled_role_refuses_the_delegation() -> None:
    outcome = await _delegate("wire_sub_disabled", enabled=False)

    assert "disabled by the organization" in outcome.text_of_events()
    assert not any("AUDITOR-ROLE-MARKER" in str(r.messages) for r in outcome.model.requests)


async def test_a_roles_turn_limit_bounds_the_delegates_cycles() -> None:
    from agent_core.subagents import SubagentRunner

    from specifications.interfaces.agent import SubagentConfig, SubagentTaskRequest

    class Looping:
        calls = 0

        async def chat_stream(self, request: Any) -> Any:
            from specifications.interfaces.llm import StreamChunk

            Looping.calls += 1
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"c{Looping.calls}",
                    "function": {"name": "think", "arguments": '{"thought": "again"}'},
                }
            )

    from agent_core.tools.registry import ToolRegistry

    config = SubagentConfig(
        id="loop", name="n", role_title="r", description="d", system_prompt="s", max_turns=2
    )
    runner = SubagentRunner(Looping(), ToolRegistry(), {"loop": config})  # type: ignore[arg-type]

    result = await runner.run(SubagentTaskRequest(role="loop", task="spin"), "session_x")

    # Two working steps, then one tool-less call for the report.
    assert result.turns_taken == 2 and Looping.calls == 3
