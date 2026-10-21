"""Tools an agent loads when it needs them: the core set, what the agent keeps active, a turn.

Every request carries the core tools and the ones the agent keeps active. Any other allowed tool
is deferred: its definition is not sent, its name is listed in the cacheable prompt, and
``load_tools`` (or calling it) loads it for the rest of the session.
"""

from __future__ import annotations

from typing import Any

import pytest
from agent_core.tool_loading import catalogue, deferred_names
from api.services.mcp_turn import McpTurnTools
from api.services.tool_loading import LOADED_TOOLS_KEY, tool_loading_for
from api.turns import runner

from specifications.interfaces.agent import (
    ACTIVE_TOOLS_WARNING,
    CORE_TOOLS,
    ServerGuidance,
    ToolLoading,
)
from specifications.interfaces.llm import ToolDefinition
from tests.turn_harness import run_turn

SEARCH = "mcp__signoz__search_logs"
AGGREGATE = "mcp__signoz__aggregate_logs"
QUERY = "mcp__grafana__query_prometheus"
MCP = (SEARCH, AGGREGATE, QUERY)


def _definition(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} does its job.",
        parameters_json_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )


class _McpServers:
    """The turn's MCP tools, the way the runner provides them."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def definitions(self) -> list[ToolDefinition]:
        return [_definition(name) for name in MCP]

    def owns(self, name: str) -> bool:
        return name in MCP

    def guidance(self) -> list[ServerGuidance]:
        return []

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append(name)
        return f"{name} answered"

    async def aclose(self) -> None:
        return None


def _rules(active: tuple[str, ...] = (), send_all: bool = False) -> frozenset[str]:
    names = ("file_read", "bash_exec", "git_status", "process_start", "load_tools", "search_tools")
    definitions = [_definition(n) for n in (*names, *MCP)]
    return deferred_names(definitions, ToolLoading(active=active, send_all=send_all))


def test_the_core_set_and_the_active_tools_are_sent_and_the_rest_waits() -> None:
    assert _rules() == {"git_status", "process_start", *MCP}, (
        "core tools (file_read, bash_exec) and the loading tools are always sent"
    )
    assert _rules(active=("mcp__signoz*__*search*", "process_start")) == {
        "git_status",
        AGGREGATE,
        QUERY,
    }, "an active tool, by name or pattern, is sent too"
    assert _rules(send_all=True) == frozenset(), "a turn with no agent sends everything"


def test_the_core_set_is_the_reviewed_nineteen() -> None:
    assert len(CORE_TOOLS) == 19 and len(set(CORE_TOOLS)) == 19
    assert {"bash_exec", "clone_repository", "git_push", "wait_for"} <= set(CORE_TOOLS)
    assert not {"process_start", "env_set", "workspace_status", "repo_foreach"} & set(CORE_TOOLS)


def test_the_catalogue_lists_names_by_server() -> None:
    listed = catalogue(MCP)

    assert "[Tools to load before use]" in listed and "load_tools" in listed
    assert f"grafana (1): {QUERY}" in listed
    assert f"signoz (2): {AGGREGATE}, {SEARCH}" in listed
    assert catalogue([]) == ""


@pytest.fixture
def mcp(monkeypatch: pytest.MonkeyPatch) -> _McpServers:
    servers = _McpServers()

    async def for_turn(*_: Any) -> McpTurnTools:
        return McpTurnTools(servers)  # type: ignore[arg-type]

    monkeypatch.setattr(runner, "_mcp_tools_for_turn", for_turn)
    return servers


def _names(request: Any) -> set[str]:
    return {t.name for t in request.tools or []}


async def test_deferred_tools_are_listed_then_loaded_for_the_rest_of_the_session(
    mcp: _McpServers,
) -> None:
    org = "org_tools_on_demand"

    outcome = await run_turn(
        org,
        script=[
            ("load_tools", {"names": [SEARCH]}),
            (SEARCH, {"q": "start_apply"}),
            (QUERY, {"q": "up"}),
        ],
    )

    first, after_load, calling, after_call = (r for r in outcome.model.requests if r.tools)
    assert not _names(first) & set(MCP), "deferred definitions are not sent"
    assert "load_tools" in _names(first)
    system = first.messages[0].content
    assert "[Tools to load before use]" in system and SEARCH in system and QUERY in system
    assert SEARCH in _names(after_load) and QUERY not in _names(after_load)
    assert QUERY not in _names(calling), "called by name from the list, before any load"
    assert QUERY in _names(after_call), "calling a listed tool loads it too"
    assert mcp.calls == [SEARCH, QUERY]
    assert outcome.session.metadata[LOADED_TOOLS_KEY] == [SEARCH, QUERY]
    assert after_call.messages[0].content == system, "the cached prefix does not change"


async def test_the_core_tools_are_sent_and_the_rest_listed_in_a_real_turn(
    mcp: _McpServers,
) -> None:
    outcome = await run_turn("org_tools_core")

    first = next(r for r in outcome.model.requests if r.tools)
    sent = _names(first)
    assert {"bash_exec", "file_edit", "clone_repository", "wait_for"} <= sent
    assert not sent & {"process_start", "env_set", "repo_foreach", *MCP}
    system = first.messages[0].content
    assert "[Tools to load before use]" in system and "process_start" in system and QUERY in system


def test_an_agent_keeps_its_own_tools_active() -> None:
    class Agent:
        active_tools = ("mcp__signoz*",)

    assert tool_loading_for(None).active == ()
    assert tool_loading_for(Agent()).active == ("mcp__signoz*",)


async def test_a_session_keeps_what_it_loaded_for_its_next_turns(mcp: _McpServers) -> None:
    org = "org_tools_resume"

    outcome = await run_turn(org, metadata={LOADED_TOOLS_KEY: [QUERY]})

    first = next(r for r in outcome.model.requests if r.tools)
    assert QUERY in _names(first) and SEARCH not in _names(first)


# -- the preview the agent settings page shows ------------------------------------------------


@pytest.fixture
def preview_client() -> Any:
    from api.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        app.state.db_manager = None
        yield client


_MEMBER = {"X-Tenant-Org-Id": "org_preview", "X-Tenant-User-Id": "u1", "X-User-Role": "developer"}
_TOOLS = ["file_read", "bash_exec", SEARCH, AGGREGATE, QUERY]


async def test_the_preview_counts_what_is_sent_and_lists_what_waits(preview_client: Any) -> None:
    reply = preview_client.post(
        "/v1/tools/loading-preview",
        json={"active_tools": [SEARCH, "mcp__grafana*"], "tool_names": [*_TOOLS, "git_status"]},
        headers=_MEMBER,
    ).json()

    assert reply["deferred"] == sorted([AGGREGATE, "git_status"])
    assert reply["sent"] == 6, "file_read, bash_exec, two active, two loading tools"
    assert reply["total"] == len(_TOOLS) + 3 and reply["warning_at"] == ACTIVE_TOOLS_WARNING
    assert "bash_exec" in reply["core"] and len(reply["core"]) == 19
    assert "6 tools are sent with every step" in reply["reason"]


async def test_the_preview_warns_past_the_active_limit(preview_client: Any) -> None:
    many = [f"mcp__big__tool_{n}" for n in range(ACTIVE_TOOLS_WARNING + 5)]
    reply = preview_client.post(
        "/v1/tools/loading-preview",
        json={"active_tools": ["mcp__big__*"], "tool_names": [*many, "git_status"]},
        headers=_MEMBER,
    ).json()

    assert reply["sent"] > ACTIVE_TOOLS_WARNING
    assert f"more than {ACTIVE_TOOLS_WARNING}" in reply["reason"]


def test_the_built_in_developer_adds_only_a_few_tools_to_the_core() -> None:
    from api.services.personas import DEFAULT_AGENT_PERSONAS

    developer = next(p for p in DEFAULT_AGENT_PERSONAS if p.id == "persona-backend")

    assert not set(developer.active_tools) & set(CORE_TOOLS), "core tools need no pin"
    assert len(developer.active_tools) + len(CORE_TOOLS) <= ACTIVE_TOOLS_WARNING
