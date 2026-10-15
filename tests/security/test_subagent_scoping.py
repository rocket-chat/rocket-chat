"""Subagent roles are per organization and their tool whitelist is enforced."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.subagents import READ_ONLY_TOOLS, SubagentRunner
from agent_core.tools.registry import ToolRegistry
from api.main import app
from fastapi.testclient import TestClient

from specifications.interfaces.agent import (
    SubagentConfig,
    SubagentTaskRequest,
    TurnCallbacks,
)
from specifications.interfaces.llm import LLMGatewayProtocol, StreamChunk

ADMIN_A = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "admin_user", "X-User-Role": "admin"}
MEMBER_A = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "member_user",
    "X-User-Role": "developer",
}
ADMIN_B = {"X-Tenant-Org-Id": "usage_org", "X-Tenant-User-Id": "usage_user", "X-User-Role": "admin"}


def _role(role_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "id": role_id,
        "name": role_id,
        "role_title": "t",
        "description": "d",
        "system_prompt": f"prompt for {role_id}",
        "whitelisted_tools": ["file_read"],
        **overrides,
    }


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        app.state.db_manager = None
        yield c


def test_one_organizations_override_does_not_change_another(client: TestClient) -> None:
    edit = client.put(
        "/v1/subagents/researcher", json=_role("researcher", max_turns=2), headers=ADMIN_A
    )
    assert edit.status_code == 200

    def researcher_turns(headers: dict[str, str]) -> int:
        roles = {r["id"]: r for r in client.get("/v1/subagents", headers=headers).json()}
        return int(roles["researcher"]["max_turns"])

    assert researcher_turns(ADMIN_A) == 2
    assert researcher_turns(ADMIN_B) == 12


def test_only_administrators_edit_roles_and_ids_are_validated(client: TestClient) -> None:
    assert client.put("/v1/subagents/x", json=_role("x"), headers=MEMBER_A).status_code == 403
    assert (
        client.put("/v1/subagents/BAD id", json=_role("BAD id"), headers=ADMIN_A).status_code == 422
    )
    assert client.put("/v1/subagents/a", json=_role("b"), headers=ADMIN_A).status_code == 422


@dataclass
class _Scripted(LLMGatewayProtocol):
    calls: list[tuple[str, dict[str, Any]]]
    seen_tools: list[list[str]] = field(default_factory=list)

    async def chat_stream(self, request: Any) -> AsyncIterator[StreamChunk]:
        self.seen_tools.append([t.name for t in request.tools])
        done = [m for m in request.messages if m.role == "tool"]
        if len(done) < len(self.calls):
            name, args = self.calls[len(done)]
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"c{len(done)}",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            )
            return
        yield StreamChunk(text_delta="done: " + " | ".join(str(m.content) for m in done))

    async def check_budget(self, *_: Any) -> bool:
        return True

    async def record_usage(self, *_: Any, **__: Any) -> None:
        return None


def _run(gateway: _Scripted, role: str, configs: dict[str, SubagentConfig] | None) -> Any:
    registry = ToolRegistry()
    runner = SubagentRunner(gateway, registry)
    ctx = ExecutionContext(
        session_id="s",
        event_queue=asyncio.Queue(),
        parent_model="m",
        callbacks=TurnCallbacks(subagent_configs=configs),
    )
    token = current_execution_ctx.set(ctx)
    try:
        return asyncio.run(runner.run(SubagentTaskRequest(role=role, task="t"), "s"))
    finally:
        current_execution_ctx.reset(token)


def test_a_subagent_cannot_call_a_tool_outside_its_whitelist() -> None:
    gateway = _Scripted(calls=[("bash_exec", {"command": "echo pwned"})])
    config = SubagentConfig(
        id="reader",
        name="r",
        role_title="r",
        description="d",
        system_prompt="p",
        whitelisted_tools=["file_read"],
    )

    result = _run(gateway, "reader", {"reader": config})

    assert "Blocked" in result.summary and "not available" in result.summary
    assert "bash_exec" not in gateway.seen_tools[0]


def test_an_invented_role_gets_read_only_tools() -> None:
    gateway = _Scripted(calls=[])

    _run(gateway, "hacker-supreme", None)

    assert set(gateway.seen_tools[0]) <= set(READ_ONLY_TOOLS)
    assert "bash_exec" not in gateway.seen_tools[0] and "file_write" not in gateway.seen_tools[0]


def test_a_disabled_role_does_not_run() -> None:
    gateway = _Scripted(calls=[])
    config = SubagentConfig(
        id="off", name="o", role_title="o", description="d", system_prompt="p", enabled=False
    )

    result = _run(gateway, "off", {"off": config})

    assert result.status == "failed" and "disabled" in result.summary
    assert gateway.seen_tools == []
