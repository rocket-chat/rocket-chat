"""A turn starts its sandbox only for work that happens in it.

The registry snapshots the workspace before a turn first changes it. Posting to Slack, acting on
GitHub, loading tools or calling an MCP server changes no file, so it must not start a sandbox
only to snapshot it: a Slack "how are you?" used to boot one.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.permissions import approval_needed, touches_workspace
from agent_core.tools.registry import ToolRegistry

from specifications.interfaces.agent import TurnCallbacks
from specifications.interfaces.llm import ToolDefinition


class _Mcp:
    def definitions(self) -> list[ToolDefinition]:
        return []

    def owns(self, name: str) -> bool:
        return name.startswith("mcp__")

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        return ""

    async def aclose(self) -> None:
        return None


def test_only_workspace_work_is_snapshotted() -> None:
    assert touches_workspace("file_write", {"path": "a"})
    assert touches_workspace("bash_exec", {"command": "make"})
    assert touches_workspace("git_commit", {"message": "m"})
    for name in (
        "slack_send_message",
        "github_pull_request",
        "load_tools",
        "execute_skill",
        "mcp__grafana__q",
    ):
        assert not touches_workspace(name, {"action": "merge"}), name
    assert approval_needed("ask", "load_tools", {"names": ["x"]}) is None, "loading is a read"


@pytest.mark.parametrize(
    ("tool", "starts_sandbox"),
    [
        ("slack_send_message", False),
        ("mcp__grafana__query", False),
        ("load_tools", False),
        ("execute_skill", False),
        ("github_pull_request", False),
        ("file_write", True),
    ],
)
async def test_a_turn_starts_its_sandbox_only_to_change_it(tool: str, starts_sandbox: bool) -> None:
    started: list[str] = []
    registry = ToolRegistry(driver=object(), session_id="s1")  # type: ignore[arg-type]

    async def post(text: str) -> str:
        return "posted"

    registry.register_dynamic_tool(
        ToolDefinition("slack_send_message", "Post.", {"type": "object"}), post
    )

    async def ensure(session_id: str) -> None:
        started.append(session_id)
        raise RuntimeError("no real sandbox here")

    ctx = ExecutionContext(
        session_id="s1",
        event_queue=asyncio.Queue(),
        parent_model="m",
        callbacks=TurnCallbacks(ensure_sandbox=ensure, external_tools=_Mcp()),
    )
    token = current_execution_ctx.set(ctx)
    try:
        await registry._checkpoint_before_first_change(tool, {"action": "merge"})
    finally:
        current_execution_ctx.reset(token)

    assert bool(started) is starts_sandbox


@pytest.mark.parametrize(
    ("runtime", "code", "starts_sandbox"),
    [("text", "", False), ("python", "def run(**_):\n    return 'ok'\n", True)],
)
async def test_a_skill_starts_the_sandbox_only_when_it_runs_code(
    runtime: str, code: str, starts_sandbox: bool
) -> None:
    from api.services import skills_lookup
    from sandbox_driver.fake_driver import FakeSandboxDriver

    from tests.turn_harness import run_turn

    org = f"org_skill_{runtime}"
    skills_lookup._memory_skills[org] = {
        "skill_1": {
            "name": "triage",
            "runtime": runtime,
            "code": code,
            "body": "# Triage\nStart with the logs.",
            "scope": "org",
        }
    }
    driver = FakeSandboxDriver()
    try:
        await run_turn(
            org,
            script=[("execute_skill", {"skill_name": "triage", "arguments": {}})],
            driver=driver,
        )
    finally:
        skills_lookup._memory_skills.pop(org, None)

    started = f"session_{org}" in driver._specs
    assert started is starts_sandbox, "instructions are read back, only code needs a sandbox"
