"""Command (stdio) MCP servers start on a turn's first call, only for agents that may use them,
and a full MCP runner is waited for, then reported, instead of silently dropping the server."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from api.services import mcp_runtime
from api.services.mcp_runtime import (
    McpServerTarget,
    McpToolProvider,
    stored_definitions,
    summarize_tool,
)
from api.services.mcp_turn import McpTurnTools, _offered_by
from api.turns import runner
from turn_harness import run_turn

SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
STORED = [{"name": "run_sql", "description": "Run SQL", "input_schema": SCHEMA}]


class _Runner:
    """A fake MCP runner: counts processes, and can be full for a number of starts."""

    def __init__(self, full_for: int = 0) -> None:
        self.started = 0
        self.stopped = 0
        self.full_for = full_for

    @asynccontextmanager
    async def __call__(self, target: McpServerTarget) -> AsyncIterator[Any]:
        if self.full_for > 0:
            self.full_for -= 1
            raise RuntimeError("The runner is at capacity; try again shortly.")
        self.started += 1
        try:
            yield SimpleNamespace(
                list_tools=self._list,
                call_tool=self._call,
            )
        finally:
            self.stopped += 1

    async def _list(self) -> Any:
        return SimpleNamespace(
            tools=[SimpleNamespace(name="run_sql", description="Run SQL", input_schema=SCHEMA)]
        )

    async def _call(self, name: str, arguments: dict[str, Any]) -> Any:
        return SimpleNamespace(
            content=[SimpleNamespace(text=f"rows for {arguments}")], is_error=False
        )


async def _no_guard(target: McpServerTarget) -> None:
    return None


def _server(name: str, tools: list[dict[str, Any]] | None = None) -> McpServerTarget:
    return McpServerTarget(
        server_id=f"id-{name}",
        name=name,
        transport="stdio",
        url="warehouse-mcp",
        stored_tools=stored_definitions(tools or []),
    )


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    mcp_runtime._listing_cache.clear()


async def test_a_stored_server_is_offered_without_starting_and_starts_on_its_first_call() -> None:
    fake = _Runner()
    provider = await McpToolProvider(
        [_server("Warehouse", STORED)], stdio_connector=fake, guard=_no_guard
    ).load()

    assert [d.name for d in provider.definitions()] == ["mcp__warehouse__run_sql"]
    assert provider.definitions()[0].parameters_json_schema == SCHEMA
    assert fake.started == 0, "advertising tools must not start the process"
    assert (await provider.call("mcp__warehouse__run_sql", {"query": "SELECT 1"})).startswith(
        "rows"
    )
    assert await provider.call("mcp__warehouse__run_sql", {"query": "SELECT 2"})
    assert fake.started == 1, "one process for the turn, started by the first call"
    await provider.aclose()
    assert fake.stopped == 1


async def test_a_server_listed_live_does_not_keep_its_process_for_the_turn() -> None:
    fake = _Runner()
    provider = await McpToolProvider(
        [_server("Warehouse")], stdio_connector=fake, guard=_no_guard
    ).load()

    assert [d.name for d in provider.definitions()] == ["mcp__warehouse__run_sql"]
    assert (fake.started, fake.stopped) == (1, 1), "the listing's process is closed at once"


async def test_a_server_the_agent_cannot_use_is_neither_listed_nor_started() -> None:
    fake = _Runner()
    offered = _offered_by(["execute_skill", "mcp__warehouse__run_sql"])
    provider = await McpToolProvider(
        [_server("Warehouse"), _server("Cluster Logs")],
        stdio_connector=fake,
        guard=_no_guard,
        offered=offered,
    ).load()

    assert [d.name for d in provider.definitions()] == ["mcp__warehouse__run_sql"]
    assert fake.started == 1, "only the warehouse was listed"
    assert _offered_by(None) is None and _offered_by(["*"]) is None


async def test_a_full_runner_is_waited_for_then_reported_to_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_runtime, "CAPACITY_RETRY_DELAYS", (0.0, 0.0, 0.0))
    waited = _Runner(full_for=2)
    provider = await McpToolProvider(
        [_server("Warehouse", STORED)], stdio_connector=waited, guard=_no_guard
    ).load()
    assert (await provider.call("mcp__warehouse__run_sql", {"query": "q"})).startswith("rows")

    full = _Runner(full_for=99)
    provider = await McpToolProvider(
        [_server("Warehouse", STORED)], stdio_connector=full, guard=_no_guard
    ).load()
    answer = await provider.call("mcp__warehouse__run_sql", {"query": "q"})
    assert "could not start" in answer and "Try again in a minute" in answer
    assert full.full_for == 99 - 5, "three waits, then the two ordinary attempts"


async def test_a_server_that_cannot_be_listed_is_named_to_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = await McpToolProvider(
        [_server("Warehouse")], stdio_connector=_Runner(full_for=1), guard=_no_guard
    ).load()
    assert provider.unavailable == ["Warehouse"] and provider.definitions() == []

    async def for_turn(*_: Any) -> McpTurnTools:
        return McpTurnTools(None, [], ["Warehouse"])

    monkeypatch.setattr(runner, "_mcp_tools_for_turn", for_turn)
    outcome = await run_turn("mcp_unreachable")
    # The note rides with the per-turn context, wherever the request carries it.
    sent = "\n".join(str(m.content) for m in outcome.model.requests[0].messages)
    assert "Unreachable this turn (their tools are missing): Warehouse" in sent


async def test_the_turn_passes_the_agents_tool_list(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[Any, ...]] = []

    async def for_turn(*args: Any) -> McpTurnTools:
        asked.append(args)
        return McpTurnTools(None)

    monkeypatch.setattr(runner, "_mcp_tools_for_turn", for_turn)
    from api.models import AgentPersonaModel
    from api.services.personas import save_persona

    agent = await save_persona(
        None,
        "mcp_allow",
        "root",
        True,
        AgentPersonaModel(
            id="persona-mcp-allow",
            name="Narrow",
            role_title="x",
            system_prompt="x",
            scope="org",
            whitelisted_tools=["think", "mcp__warehouse__run_sql"],
        ),
    )
    await run_turn("mcp_allow", metadata={"agent_id": agent.id})

    assert asked and "mcp__warehouse__run_sql" in (asked[0][4] or [])


def test_a_probe_stores_each_tools_schema_unless_it_is_huge() -> None:
    small = summarize_tool(SimpleNamespace(name="a", description="", input_schema=SCHEMA))
    huge_schema = {"type": "object", "description": "x" * 40_000}
    huge = summarize_tool(SimpleNamespace(name="b", description="", input_schema=huge_schema))

    assert small["input_schema"] == SCHEMA and "input_schema" not in huge
    assert stored_definitions([small, huge]) == (), "one schema missing: the server is listed live"
