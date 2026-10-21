"""Tools an agent loads when it needs them: three states, the organization's settings, a turn.

A deferred tool is allowed but its definition is not sent: its name is listed in the cacheable
prompt, ``load_tools`` (or calling it) loads it for the rest of the session.
"""

from __future__ import annotations

from typing import Any

import pytest
from agent_core.tool_loading import catalogue, deferred_names
from api.services.mcp_turn import McpTurnTools
from api.services.settings import save_org_config
from api.services.tool_loading import LOADED_TOOLS_KEY, tool_loading_for
from api.turns import runner

from specifications.interfaces.agent import ServerGuidance, ToolLoading
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


def _rules(mode: str, active: tuple[str, ...] = (), threshold: int = 60) -> frozenset[str]:
    platform = [_definition(n) for n in ("file_read", "bash_exec", "load_tools", "search_tools")]
    definitions = platform + [_definition(n) for n in MCP]
    return deferred_names(
        definitions, ToolLoading(mode=mode, active=active, threshold=threshold), lambda n: n in MCP
    )


def test_the_four_modes() -> None:
    platform = {"file_read", "bash_exec"}
    assert _rules("all") == frozenset()
    assert _rules("on_demand") == frozenset(MCP), "MCP tools wait, the platform's tools do not"
    assert _rules("on_demand", active=("mcp__signoz*__*search*",)) == {AGGREGATE, QUERY}, (
        "a pinned MCP tool is loaded and the platform's tools stay loaded"
    )
    assert _rules("on_demand", active=("file_read",)) == frozenset(MCP), (
        "pinning a platform tool changes nothing: it is loaded already"
    )
    assert _rules("pinned", active=("mcp__signoz*__*search*",)) == platform | {AGGREGATE, QUERY}, (
        "pinned loads only the pins (and the loading tools)"
    )
    assert _rules("pinned") == platform | set(MCP), "with no pins only the loading tools are loaded"
    assert _rules("auto", threshold=60) == frozenset(), "few tools: nothing deferred"
    assert _rules("auto", threshold=5) == frozenset(MCP)
    assert _rules("auto", active=(QUERY,), threshold=5) == {SEARCH, AGGREGATE}


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


@pytest.mark.setting("general.tool_loading")
async def test_on_demand_tools_are_listed_then_loaded_for_the_rest_of_the_session(
    mcp: _McpServers,
) -> None:
    org = "org_tools_on_demand"
    await save_org_config(None, org, "general", {"tool_loading": "on_demand"}, [])

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


@pytest.mark.setting("general.tool_loading")
async def test_loading_all_tools_sends_every_definition(mcp: _McpServers) -> None:
    org = "org_tools_all"
    await save_org_config(None, org, "general", {"tool_loading": "all"}, [])

    outcome = await run_turn(org)

    first = next(r for r in outcome.model.requests if r.tools)
    assert set(MCP) <= _names(first)
    assert "[Tools to load before use]" not in first.messages[0].content


@pytest.mark.setting("general.tool_loading_threshold")
async def test_auto_defers_mcp_tools_only_past_the_threshold(mcp: _McpServers) -> None:
    few, many = "org_tools_auto_high", "org_tools_auto_low"
    await save_org_config(
        None, few, "general", {"tool_loading": "auto", "tool_loading_threshold": 500}, []
    )
    await save_org_config(
        None, many, "general", {"tool_loading": "auto", "tool_loading_threshold": 10}, []
    )

    under = next(r for r in (await run_turn(few)).model.requests if r.tools)
    over = next(r for r in (await run_turn(many)).model.requests if r.tools)

    assert set(MCP) <= _names(under)
    assert not _names(over) & set(MCP) and "file_read" in _names(over)


async def test_an_agent_overrides_the_organizations_mode_and_keeps_its_own_tools_loaded() -> None:
    org = "org_tools_agent"
    await save_org_config(None, org, "general", {"tool_loading": "all"}, [])

    class Agent:
        tool_loading = "on_demand"
        active_tools = ("mcp__signoz*",)

    inherited = await tool_loading_for(None, org, None)
    own = await tool_loading_for(None, org, Agent())

    assert inherited.mode == "all" and inherited.threshold == 60
    assert own.mode == "on_demand" and own.active == ("mcp__signoz*",)


async def test_a_session_keeps_what_it_loaded_for_its_next_turns(mcp: _McpServers) -> None:
    org = "org_tools_resume"
    await save_org_config(None, org, "general", {"tool_loading": "on_demand"}, [])

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


@pytest.mark.setting("general.tool_loading")
async def test_the_preview_applies_the_organizations_mode_to_an_agent_that_has_none(
    preview_client: Any,
) -> None:
    await save_org_config(None, "org_preview", "general", {"tool_loading": "on_demand"}, [])

    reply = preview_client.post(
        "/v1/tools/loading-preview", json={"tool_names": _TOOLS}, headers=_MEMBER
    ).json()

    assert reply["mode"] == "on_demand" and reply["source"] == "organization"
    assert reply["deferred"] == sorted(MCP), "MCP tools wait; the platform's do not"
    assert "MCP tools are listed by name" in reply["reason"]


async def test_the_preview_follows_the_agents_own_mode_and_pins(preview_client: Any) -> None:
    await save_org_config(None, "org_preview", "general", {"tool_loading": "all"}, [])
    pins = [SEARCH, "mcp__grafana*"]

    additive = preview_client.post(
        "/v1/tools/loading-preview",
        json={"tool_loading": "on_demand", "active_tools": pins, "tool_names": _TOOLS},
        headers=_MEMBER,
    ).json()
    only_pinned = preview_client.post(
        "/v1/tools/loading-preview",
        json={"tool_loading": "pinned", "active_tools": pins, "tool_names": _TOOLS},
        headers=_MEMBER,
    ).json()

    assert additive["source"] == "agent" and only_pinned["source"] == "agent"
    assert additive["deferred"] == [AGGREGATE], (
        "the platform's tools stay loaded; pins add MCP tools"
    )
    assert only_pinned["deferred"] == sorted(["file_read", "bash_exec", AGGREGATE]), (
        "with pinned only the pins (and the loading tools) stay loaded"
    )
    assert "2 pinned tools" in only_pinned["reason"] and "plus 2 pinned" in additive["reason"]


@pytest.mark.setting("general.tool_loading_threshold")
async def test_the_automatic_mode_is_decided_by_the_organizations_limit(
    preview_client: Any,
) -> None:
    body = {"tool_loading": "auto", "tool_names": _TOOLS}
    await save_org_config(None, "org_preview", "general", {"tool_loading_threshold": 500}, [])
    within = preview_client.post("/v1/tools/loading-preview", json=body, headers=_MEMBER).json()
    await save_org_config(None, "org_preview", "general", {"tool_loading_threshold": 3}, [])
    over = preview_client.post("/v1/tools/loading-preview", json=body, headers=_MEMBER).json()

    assert within["deferred"] == [] and "within the limit of 500" in within["reason"]
    assert over["deferred"] == sorted(MCP) and "over the limit of 3" in over["reason"]
    assert over["total"] == len(_TOOLS) + 2, "the loading tools count against the limit"


async def test_the_preview_says_so_when_an_agent_is_over_the_limit_with_nothing_to_defer(
    preview_client: Any,
) -> None:
    await save_org_config(None, "org_preview", "general", {"tool_loading_threshold": 2}, [])

    reply = preview_client.post(
        "/v1/tools/loading-preview",
        json={"tool_loading": "auto", "tool_names": ["file_read", "bash_exec", "git_status"]},
        headers=_MEMBER,
    ).json()

    assert reply["deferred"] == []
    assert "over the limit of 2" in reply["reason"] and "no MCP tools to defer" in reply["reason"]


def test_the_built_in_developer_keeps_its_lean_core_as_a_pinned_agent() -> None:
    from api.services.personas import DEFAULT_AGENT_PERSONAS

    developer = next(p for p in DEFAULT_AGENT_PERSONAS if p.id == "persona-backend")

    assert developer.tool_loading == "pinned"
    assert "file_read" in developer.active_tools and "bash_exec" in developer.active_tools
