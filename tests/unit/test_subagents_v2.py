"""Subagents v2 through whole turns: typed roles, the structured report, nested steps, gates a
role may not raise, budgets, isolation that fails closed, and the roles a repository defines."""

from __future__ import annotations

from typing import Any

from agent_core.subagent_context import REFUSED_GATE
from agent_core.subagent_roles import DEFAULT_SUBAGENT_CONFIGS, parse_role_file, with_role_enum
from api.models import SubagentConfigModel
from api.services.session_support import ACTIVE_REPOSITORY_KEY
from api.services.subagents import reset_memory_subagents, save_subagent_config
from api.turns.subagent_callbacks import child_step, parallel_writers, session_event
from slack_assistant.turn_stream import subagent_completed_line, subagent_started_line

from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.llm import ToolDefinition
from tests.turn_harness import run_turn


def _completed(outcome: Any) -> dict[str, Any]:
    done = [e for e in outcome.events if e.event_type == "subagent_completed"]
    assert done, "the child announces how it ended"
    return dict(done[0].payload)


def _delegation_result(outcome: Any) -> str:
    calls = [
        e
        for e in outcome.events
        if e.event_type == "tool_completed"
        and e.payload.get("name") == "delegate_subagent"
        and not e.payload.get("subagent_id")
    ]
    assert calls, "the parent's delegate_subagent call completes"
    return str(calls[0].payload["result"])


async def test_a_child_ends_with_a_structured_report_the_parent_reads() -> None:
    outcome = await run_turn(
        "subv2_report",
        steps=[
            ("call", "delegate_subagent", {"role": "reviewer", "task": "Review the diff"}),
            (
                "call",
                "report",
                {
                    "status": "completed",
                    "summary": "One bug in greet.",
                    "findings": [
                        {
                            "file": "src/app.py",
                            "line": 2,
                            "severity": "high",
                            "evidence": "None crashes",
                        },
                        {"file": "src/util.py", "line": 1, "severity": "low", "evidence": "naming"},
                    ],
                    "next_steps": ["Guard against None"],
                },
            ),
            ("say", "Thanks, fixing it."),
        ],
    )

    report = _completed(outcome)["report"]
    assert report["status"] == "completed"
    assert [f["severity"] for f in report["findings"]] == ["high", "low"]
    shown = _delegation_result(outcome)
    assert "[REVIEWER SUBAGENT REPORT]" in shown
    # The parent reads the findings ranked by severity, high first.
    assert shown.index("src/app.py:2") < shown.index("src/util.py:1")
    assert "Guard against None" in shown


async def test_a_child_without_a_report_is_asked_once_then_its_text_is_kept() -> None:
    outcome = await run_turn(
        "subv2_nudge",
        steps=[
            ("call", "delegate_subagent", {"role": "researcher", "task": "What is X?"}),
            ("say", "X is a thing."),
            ("say", "Still just text."),
            ("say", "Done."),
        ],
    )

    child = [
        r
        for r in outcome.model.requests
        if str(r.messages[0].content).startswith("You are a researcher")
    ]
    nudged = [r for r in child if "Call `report` now" in str(r.messages[-1].content)]
    assert len(nudged) == 1
    assert _completed(outcome)["summary"] == "Still just text."


async def test_a_childs_steps_are_published_under_the_delegation_never_as_the_turns() -> None:
    outcome = await run_turn(
        "subv2_nested",
        steps=[
            ("call", "delegate_subagent", {"role": "explorer", "task": "Find greet"}),
            ("call", "grep_search", {"pattern": "def greet"}),
            ("call", "report", {"status": "completed", "summary": "src/app.py:1"}),
            ("say", "Found it."),
        ],
    )

    nested = [e for e in outcome.events if e.payload.get("subagent_id")]
    started = [e for e in nested if e.event_type == "tool_started"]
    assert started and started[0].payload["name"] == "grep_search"
    assert started[0].payload["parent_tool_call_id"], "a child's step names the call it runs under"
    recorded = [t["name"] for t in outcome.session.metadata.get("executed_tools", [])]
    assert "grep_search" not in recorded


async def test_a_role_that_may_not_ask_has_its_approvals_refused() -> None:
    outcome = await run_turn(
        "subv2_gate",
        steps=[
            ("call", "delegate_subagent", {"role": "researcher", "task": "Do something risky"}),
            ("call", "request_approval", {"action_type": "drop_db", "description": "drop it"}),
            ("call", "report", {"status": "blocked", "summary": "Needs approval."}),
            ("say", "I will ask myself."),
        ],
    )

    assert not outcome.events_of("approval_required"), "no gate reaches the person"
    child_results = [
        e.payload["result"]
        for e in outcome.events
        if e.event_type == "tool_completed" and e.payload.get("subagent_id")
    ]
    assert (
        any(REFUSED_GATE in str(r) for r in child_results)
        or _completed(outcome)["status"] == "blocked"
    )


async def test_an_unknown_role_is_refused_with_the_roles_there_are() -> None:
    outcome = await run_turn(
        "subv2_unknown",
        steps=[
            ("call", "delegate_subagent", {"role": "wizard", "task": "Anything"}),
            ("say", "OK."),
        ],
    )

    shown = _delegation_result(outcome)
    assert "no 'wizard' subagent role" in shown
    assert "reviewer" in shown
    assert not [e for e in outcome.events if e.event_type == "subagent_started"]


async def test_a_role_working_in_its_own_tree_fails_rather_than_writing_to_the_shared_one() -> None:
    outcome = await run_turn(
        "subv2_isolation",
        steps=[
            ("call", "delegate_subagent", {"role": "implementer", "task": "Change greet"}),
            ("say", "The implementer could not run."),
        ],
    )

    done = _completed(outcome)
    assert done["status"] == "failed"
    assert "Nothing was written to your tree" in done["summary"]
    assert not any(
        str(r.messages[0].content).startswith("You are an implementer")
        for r in outcome.model.requests
    ), "the child never called the model"


async def test_a_child_stops_at_its_own_budget_and_reports() -> None:
    reset_memory_subagents()
    org = "subv2_budget"
    await save_subagent_config(
        None,
        org,
        SubagentConfigModel(
            id="spender",
            name="Spender",
            role_title="Spender",
            description="Spends.",
            system_prompt="You spend.",
            whitelisted_tools=["grep_search"],
            max_cost_usd=0.000001,
        ),
    )
    usage = {"prompt_tokens": 100_000, "completion_tokens": 10_000}
    outcome = await run_turn(
        org,
        steps=[
            ("call", "delegate_subagent", {"role": "spender", "task": "Search"}),
            ("call", "grep_search", {"pattern": "x"}, usage),
            ("say", "Stopped: reached its budget."),
            ("say", "OK."),
        ],
    )

    done = _completed(outcome)
    assert done["status"] == "partial"
    assert done["cost"] > 0.000001
    assert any("reached your limit" in str(r.messages[-1].content) for r in outcome.model.requests)
    reset_memory_subagents()


def test_the_delegation_tool_lists_the_roles_with_tiers_and_isolation() -> None:
    definition = ToolDefinition(
        "delegate_subagent",
        "Delegate.",
        {"type": "object", "properties": {"role": {"type": "string"}}},
    )
    shown = with_role_enum(definition, DEFAULT_SUBAGENT_CONFIGS)

    assert "`explorer` (fast)" in shown.description
    assert "`implementer` (default, worktree)" in shown.description
    assert shown.parameters_json_schema["properties"]["role"]["enum"] == sorted(
        DEFAULT_SUBAGENT_CONFIGS
    )
    assert len(shown.description) < 4_000, "the index is bounded however many roles there are"


async def test_the_children_inherit_the_repository_instructions_and_the_checklist() -> None:
    outcome = await run_turn(
        "subv2_inherit",
        steps=[
            (
                "call",
                "update_task_checklist",
                {"tasks": [{"id": "1", "title": "Fix greet", "status": "in_progress"}]},
            ),
            ("call", "delegate_subagent", {"role": "researcher", "task": "Look"}),
            ("call", "report", {"status": "completed", "summary": "Looked."}),
            ("say", "Done."),
        ],
        metadata={ACTIVE_REPOSITORY_KEY: "/workspace"},
    )

    child_system = outcome.model.requests[2].messages[0].content
    assert "[in_progress] Fix greet" in child_system


def test_a_repository_role_file_from_the_cockpit_export_is_read() -> None:
    exported = (
        '---\nid: docs_writer\nkind: subagent\nname: "Docs writer"\n'
        "whitelisted_tools: [file_read, file_write]\nmax_turns: 12\ntier: fast\n---\n\nWrite docs."
    )
    role = parse_role_file(".rocket/agents/docs_writer.md", exported)
    assert role is not None
    assert (role.id, role.name, role.tier, role.max_turns) == (
        "docs_writer",
        "Docs writer",
        "fast",
        12,
    )
    assert role.whitelisted_tools == ["file_read", "file_write"]
    assert role.source == "repository" and not role.can_ask_user

    agent = "---\nid: persona-x\nkind: agent\n---\n\nAn agent, not a role."
    assert parse_role_file(".rocket/agents/persona-x.md", agent) is None


def test_a_childs_events_map_to_nested_session_events() -> None:
    started = AgentEvent(
        event_type="subagent_tool_call",
        session_id="s",
        payload={
            "id": "c1",
            "name": "grep_search",
            "arguments": '{"pattern": "x"}',
            "subagent_id": "sub_1",
            "parent_tool_call_id": "p1",
        },
    )
    mapped = child_step(started)
    assert mapped is not None
    event_type, payload = mapped
    assert event_type == "tool_started"
    assert payload["arguments"] == {"pattern": "x"} and payload["parent_tool_call_id"] == "p1"
    # A background child's model usage stays internal.
    assert session_event(AgentEvent("model_usage", "s", {"subagent_id": "sub_1"})) is None


def test_parallel_writers_follow_the_session_sandbox_size() -> None:
    assert parallel_writers(1.0, "2Gi") == 1
    assert parallel_writers(4.0, "8Gi") == 2
    assert parallel_writers(16.0, "64Gi") == 4


def test_slack_shows_one_line_per_subagent() -> None:
    assert (
        subagent_started_line({"role": "reviewer", "task": "check  the\ndiff"})
        == "Reviewer: check the diff"
    )
    line = subagent_completed_line(
        {
            "role": "reviewer",
            "status": "completed",
            "report": {"summary": "3 findings, one high\nmore"},
            "cost": 0.041,
        }
    )
    assert line == "Reviewer done: 3 findings, one high · $0.04"
    assert (
        subagent_completed_line({"role": "qa_verifier", "status": "failed"}) == "Qa verifier failed"
    )
