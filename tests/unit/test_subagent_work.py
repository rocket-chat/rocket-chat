"""Delegates that can do their job: the right tools, the workspace, the main turn's limits."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from agent_core.dispatcher import ToolDispatcher
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.subagents import DEFAULT_SUBAGENT_CONFIGS, FINAL_REPORT_NOTE, SubagentRunner
from agent_core.tools.registry import ToolRegistry
from api.services.session_support import ACTIVE_REPOSITORY_KEY
from api.services.transcript import InMemoryTranscriptStore
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import SubagentConfig, SubagentTaskRequest
from specifications.interfaces.llm import ModelRequest, StreamChunk
from specifications.interfaces.sandbox import ExecResult
from tests.turn_harness import run_turn


def test_the_built_in_roles_can_search_and_test() -> None:
    searching = {"grep_search", "glob_files", "list_dir", "code_search", "file_read"}
    for role in ("researcher", "security_auditor"):
        assert searching <= set(DEFAULT_SUBAGENT_CONFIGS[role].whitelisted_tools)
    assert {"run_tests", "grep_search", "list_dir"} <= set(
        DEFAULT_SUBAGENT_CONFIGS["qa_verifier"].whitelisted_tools
    )
    assert all(config.max_turns == 12 for config in DEFAULT_SUBAGENT_CONFIGS.values())


async def test_a_delegate_searches_the_active_repository_and_reports() -> None:
    driver = FakeSandboxDriver()
    driver.script_command("rg ", ExecResult(0, "src/auth.py:3:def login():\n", "", 1))

    outcome = await run_turn(
        "org_delegate",
        script=[
            ("delegate_subagent", {"role": "researcher", "task": "Where is login defined?"}),
            ("grep_search", {"pattern": "def login"}),
        ],
        metadata={ACTIVE_REPOSITORY_KEY: "/workspace/api"},
        driver=driver,
    )

    subagent_request = outcome.model.requests[1]
    system = subagent_request.messages[0].content
    assert "The active repository is /workspace/api" in system
    assert "Your tools are read-only" in system
    searches = [c for _, c in driver.exec_calls if c.startswith("rg ") or " rg " in c]
    assert searches and "/workspace/api" in searches[0], "relative paths use the active repository"
    completed = [e for e in outcome.events if e.event_type == "subagent_completed"]
    assert completed and completed[0].payload["tool_calls_count"] == 1


class _AlwaysCallsTools:
    """Keeps calling a tool while it may; writes a report when it may not."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        if not request.tools:
            yield StreamChunk(text_delta="Report: checked a and b; c is unverified.")
            return
        yield StreamChunk(
            text_delta="still looking",
            tool_call_delta={
                "index": 0,
                "id": f"c{len(self.requests)}",
                "function": {"name": "slow_tool", "arguments": "{}"},
            },
        )


class _SlowRegistry(ToolRegistry):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        await asyncio.sleep(5)
        return "never"


def _role(max_turns: int) -> dict[str, SubagentConfig]:
    return {
        "worker": SubagentConfig(
            id="worker", name="W", role_title="W", description="", system_prompt="Work.",
            max_turns=max_turns, whitelisted_tools=["slow_tool"],
        )
    }  # fmt: skip


async def test_a_delegate_that_runs_out_of_steps_still_reports() -> None:
    gateway = _AlwaysCallsTools()
    registry = _SlowRegistry()
    registry.get_tool_definitions = lambda: [  # type: ignore[method-assign]
        type("T", (), {"name": "slow_tool"})()
    ]
    dispatcher = ToolDispatcher(registry=registry, tool_timeout_seconds=1)
    runner = SubagentRunner(gateway, registry, configs=_role(2), dispatcher=dispatcher)

    result = await runner.run(SubagentTaskRequest(role="worker", task="t"), session_id="s")

    assert result.summary == "Report: checked a and b; c is unverified."
    assert gateway.requests[-1].messages[-1].content == FINAL_REPORT_NOTE
    tool_results = [m.content for m in gateway.requests[-1].messages if m.role == "tool"]
    assert tool_results == ["Tool execution timed out after 1s"] * 2, "the main turn's budget"


async def test_the_organizations_step_limit_is_what_the_delegate_gets() -> None:
    gateway = _AlwaysCallsTools()
    registry = _SlowRegistry()
    registry.call_tool = lambda name, arguments: asyncio.sleep(0, "ok")  # type: ignore[method-assign]
    registry.get_tool_definitions = lambda: [  # type: ignore[method-assign]
        type("T", (), {"name": "slow_tool"})()
    ]

    await SubagentRunner(gateway, registry, configs=_role(3)).run(
        SubagentTaskRequest(role="worker", task="t"), session_id="s"
    )

    with_tools = [r for r in gateway.requests if r.tools]
    assert len(with_tools) == 3 and len(gateway.requests) == 4


def test_delegates_share_the_main_turns_dispatcher() -> None:
    orchestrator = AsyncReActOrchestrator(
        gateway=_AlwaysCallsTools(),  # type: ignore[arg-type]
        registry=ToolRegistry(),
        transcript_store=InMemoryTranscriptStore(),
    )

    assert orchestrator.subagent_runner.dispatcher is orchestrator.dispatcher
