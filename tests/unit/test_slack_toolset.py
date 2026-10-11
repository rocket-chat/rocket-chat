"""Slack toolset scoping and outbound DM policy.

Slack tools are a session-scoped *toolset*: they are invisible and uncallable unless the
turn enables the toolset, so they never leak into web sessions. Outbound tools are
policy-gated, and direct messages obey the org DM policy (none / paired / all).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from slack_assistant.assistant import SlackAssistant
from slack_assistant.config import SlackAssistantSettings

from specifications.interfaces.llm import (
    LLMGatewayProtocol,
    ModelRequest,
    StreamChunk,
    ToolDefinition,
)


class _CapturingGateway(LLMGatewayProtocol):
    """Records the tool names offered on each request, then answers immediately."""

    def __init__(self) -> None:
        self.tools_seen: list[list[str]] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.tools_seen.append([tool.name for tool in (request.tools or [])])
        yield StreamChunk(text_delta="done")
        yield StreamChunk(is_finished=True, finish_reason="stop")

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        return None


@pytest.mark.asyncio
async def test_wildcard_web_turn_never_receives_slack_tools() -> None:
    """A web turn (no enabled toolsets) must not get Slack tools even with a '*' agent.

    Toolset gating happens in the registry, *before* the persona whitelist is applied, so a
    wildcard allow-list cannot surface transport tools into a regular web session.
    """
    registry = ToolRegistry()

    async def _handler() -> str:
        return "ok"

    registry.register_dynamic_tool(
        ToolDefinition(name="slack_probe", description="", parameters_json_schema={}),
        _handler,
        toolset="slack",
    )
    gateway = _CapturingGateway()
    orchestrator = AsyncReActOrchestrator(gateway=gateway, registry=registry)

    async for _event in orchestrator.process_user_turn(
        "s_web",
        "hello",
        whitelisted_tools=["*"],
        enabled_toolsets=frozenset(),
        max_turns=1,
    ):
        pass

    assert gateway.tools_seen, "the model should have been called"
    for offered in gateway.tools_seen:
        assert "slack_probe" not in offered
    # Sanity: the wildcard still exposes ordinary tools.
    assert any("bash_exec" in offered for offered in gateway.tools_seen)


def _ctx(**overrides: Any) -> ExecutionContext:
    base: dict[str, Any] = {
        "session_id": "s1",
        "event_queue": asyncio.Queue(),
        "parent_model": "m",
    }
    base.update(overrides)
    return ExecutionContext(**base)


def test_toolset_tools_are_invisible_until_enabled() -> None:
    registry = ToolRegistry()

    async def _handler() -> str:
        return "ok"

    registry.register_dynamic_tool(
        ToolDefinition(name="slack_probe", description="", parameters_json_schema={}),
        _handler,
        toolset="slack",
    )

    def visible() -> set[str]:
        return {d.name for d in registry.get_tool_definitions()}

    assert "slack_probe" not in visible()

    token = current_execution_ctx.set(_ctx(enabled_toolsets=frozenset({"slack"})))
    try:
        assert "slack_probe" in visible()
    finally:
        current_execution_ctx.reset(token)


@pytest.mark.asyncio
async def test_toolset_call_is_blocked_without_the_toolset() -> None:
    registry = ToolRegistry()

    async def _handler() -> str:
        return "ok"

    registry.register_dynamic_tool(
        ToolDefinition(name="slack_probe", description="", parameters_json_schema={}),
        _handler,
        toolset="slack",
    )

    blocked = await registry.call_tool("slack_probe", {})
    assert blocked.startswith("Blocked:")

    token = current_execution_ctx.set(_ctx(enabled_toolsets=frozenset({"slack"})))
    try:
        assert await registry.call_tool("slack_probe", {}) == "ok"
    finally:
        current_execution_ctx.reset(token)


def _assistant(**settings: Any) -> SlackAssistant:
    return SlackAssistant(orchestrator=MagicMock(), settings=SlackAssistantSettings(**settings))


@pytest.mark.asyncio
async def test_dm_policy_none_blocks_direct_messages() -> None:
    assistant = _assistant(slack_dm_policy="none")
    result = await assistant._tool_send_dm("U123", "hello")
    assert "disabled" in result and "dm=NO" in result


@pytest.mark.asyncio
async def test_dm_policy_paired_only_blocks_unpaired_recipients() -> None:
    assistant = _assistant(slack_dm_policy="paired")
    result = await assistant._tool_send_dm("U_UNPAIRED", "hello")
    assert "PAIRED ONLY" in result


@pytest.mark.asyncio
async def test_dm_policy_all_allows_reaching_the_client() -> None:
    assistant = _assistant(slack_dm_policy="all")
    # With no connected client the call proceeds past policy and fails at the transport,
    # proving the policy did not block it.
    result = await assistant._tool_send_dm("U_ANY", "hello")
    assert "not connected" in result


@pytest.mark.asyncio
async def test_outbound_channel_allowlist_blocks_unlisted_channels() -> None:
    assistant = _assistant(slack_outbound_channels="C_ALLOWED")
    blocked = await assistant._tool_send_message("C_OTHER", "hello")
    assert blocked.startswith("Blocked:")
    # The allowlisted channel passes policy and reaches the (absent) transport.
    allowed = await assistant._tool_send_message("C_ALLOWED", "hello")
    assert "not connected" in allowed


def test_dm_policy_normalises_unknown_values_to_paired() -> None:
    assert _assistant(slack_dm_policy="garbage")._dm_policy() == "paired"
    assert _assistant(slack_dm_policy="ALL")._dm_policy() == "all"


def test_registry_catalog_includes_toolset_tools() -> None:
    registry = ToolRegistry()

    async def _handler() -> str:
        return "ok"

    registry.register_dynamic_tool(
        ToolDefinition(name="slack_probe", description="probe", parameters_json_schema={}),
        _handler,
        toolset="slack",
    )

    names = {entry.name for entry in registry.catalog()}
    assert "slack_probe" in names
    assert "bash_exec" in names  # built-in tools are listed too
    assert registry.toolset_of("slack_probe") == "slack"


def test_slack_settings_domain_exposes_the_outbound_policy() -> None:
    from config_engine.cascading import SYSTEM_DOMAIN_DEFAULTS

    slack = SYSTEM_DOMAIN_DEFAULTS["slack"]
    assert slack["dm_policy"] == "paired"
    assert slack["outbound_channels"] == []


def test_per_session_policy_overrides_the_env_default() -> None:
    assistant = _assistant(slack_dm_policy="all")
    token = current_execution_ctx.set(_ctx(session_id="s1"))
    try:
        assistant._session_policy["s1"] = ("none", set())
        assert assistant._dm_policy() == "none"
        # No allowlist: default-deny, except the conversation the agent was invoked from.
        assert assistant._outbound_channel_allowed("C1") is False
        assistant._session_meta["s1"] = ("C1", "1.1")
        assert assistant._outbound_channel_allowed("C1") is True
        assert assistant._outbound_channel_allowed("C9") is False

        assistant._session_policy["s1"] = ("all", {"C1"})
        assert assistant._outbound_channel_allowed("C1") is True
        assert assistant._outbound_channel_allowed("C2") is False
    finally:
        current_execution_ctx.reset(token)
