"""The agent or model chosen in the composer binds one turn, and the history keeps it.

A turn names its agent (or asks for a raw model) itself, so a selection changed in another tab
or a PATCH still in flight cannot change which agent answers. Each turn records the agent and
model it ran with on its user message, so the history does not follow later selections.
"""

from __future__ import annotations

from typing import Any

from api.models import AgentPersonaModel
from api.services.personas import save_persona
from api.store import InMemorySessionStore
from api.turns.context import TurnSelection
from turn_harness import TurnOutcome, run_turn

from specifications.interfaces.events import EventType


async def _agent(org: str, name: str, marker: str) -> str:
    persona = AgentPersonaModel(
        id=f"persona-{org}-{name.lower()}",
        name=name,
        role_title=name,
        system_prompt=f"You are {marker}.",
        model=f"{name.lower()}/model",
        whitelisted_tools=["file_read"],
        scope="org",
    )
    return (await save_persona(None, org, "admin", True, persona)).id


def _system(outcome: TurnOutcome) -> str:
    request = next(r for r in outcome.model.requests if r.tools)
    return "\n".join(m.content or "" for m in request.messages if m.role == "system")


def _running(outcome: TurnOutcome) -> dict[str, Any]:
    return next(
        e.payload
        for e in outcome.events
        if e.event_type == EventType.STATUS_CHANGED and e.payload.get("status") == "running"
    )


async def test_the_turns_own_agent_answers_whatever_the_session_says() -> None:
    org = "select_agent"
    reviewer = await _agent(org, "Reviewer", "REVIEWER-MARKER")
    fixer = await _agent(org, "Fixer", "FIXER-MARKER")

    outcome = await run_turn(
        org, metadata={"agent_id": reviewer}, selection=TurnSelection(agent_id=fixer)
    )

    assert "FIXER-MARKER" in _system(outcome) and "REVIEWER-MARKER" not in _system(outcome)
    assert _running(outcome) | {"message": ""} == {
        "status": "running",
        "message": "",
        "agent_id": fixer,
        "agent_name": "Fixer",
        "model": "fixer/model",
    }
    asked = next(m for m in outcome.session.conversation_history if m.role == "user")
    assert (asked.agent_id, asked.agent_name, asked.model) == (fixer, "Fixer", "fixer/model")
    assert outcome.session.metadata["agent_id"] == fixer, "the session's next turn starts there"


async def test_a_raw_turn_runs_no_agent_on_the_chosen_model_with_every_tool_loaded() -> None:
    org = "select_raw"
    reviewer = await _agent(org, "Reviewer", "REVIEWER-MARKER")

    outcome = await run_turn(
        org,
        metadata={"agent_id": reviewer, "tool_loading": "on_demand"},
        selection=TurnSelection(raw=True),
        model_choice="raw/model-z",
    )

    request = next(r for r in outcome.model.requests if r.tools)
    assert request.model == "raw/model-z"
    assert "REVIEWER-MARKER" not in _system(outcome), "no agent prompt"
    assert {t.name for t in request.tools or []} >= {"file_read", "bash_exec", "web_search"}
    assert "[Tools to load before use]" not in _system(outcome), "every tool loaded"
    asked = next(m for m in outcome.session.conversation_history if m.role == "user")
    assert (asked.agent_id, asked.agent_name, asked.model) == ("", "", "raw/model-z")
    assert outcome.session.metadata["inference_mode"] == "direct"
    assert outcome.session.metadata["agent_id"] == reviewer, "the agent stays for later turns"


async def test_each_turn_keeps_the_agent_that_answered_it() -> None:
    org = "select_history"
    reviewer = await _agent(org, "Reviewer", "REVIEWER-MARKER")
    fixer = await _agent(org, "Fixer", "FIXER-MARKER")
    store = InMemorySessionStore()

    await run_turn(org, store=store, selection=TurnSelection(agent_id=reviewer))
    outcome = await run_turn(org, store=store, selection=TurnSelection(agent_id=fixer))

    asked = [m.agent_name for m in outcome.session.conversation_history if m.role == "user"]
    assert asked == ["Reviewer", "Fixer"]


async def test_a_turn_without_a_choice_follows_the_sessions_mode() -> None:
    org = "select_follow"
    reviewer = await _agent(org, "Reviewer", "REVIEWER-MARKER")

    outcome = await run_turn(
        org,
        metadata={"agent_id": reviewer, "inference_mode": "direct"},
        model_choice="raw/model-z",
    )

    assert "REVIEWER-MARKER" not in _system(outcome), "a raw session stays raw (API, Slack)"
