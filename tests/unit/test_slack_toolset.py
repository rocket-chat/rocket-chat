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


@pytest.mark.asyncio
async def test_per_session_policy_overrides_the_env_default() -> None:
    assistant = _assistant(slack_dm_policy="all")
    token = current_execution_ctx.set(_ctx(session_id="s1"))
    try:
        assistant._session_policy["s1"] = ("none", set())
        assert assistant._dm_policy() == "none"
        # No allowlist: default-deny, except the conversation the agent was invoked from.
        assert await assistant._outbound_channel_allowed("C1") is False
        assistant._session_meta["s1"] = ("C1", "1.1")
        assert await assistant._outbound_channel_allowed("C1") is True
        assert await assistant._outbound_channel_allowed("C9") is False

        assistant._session_policy["s1"] = ("all", {"C1"})
        assert await assistant._outbound_channel_allowed("C1") is True
        assert await assistant._outbound_channel_allowed("C2") is False
    finally:
        current_execution_ctx.reset(token)


def _invited_to(assistant: SlackAssistant, channels: dict[str, dict[str, Any]]) -> list[str]:
    """Slack answers conversations.info from ``channels``; returns the channels asked about."""
    asked: list[str] = []

    async def conversations_info(channel: str) -> dict[str, Any]:
        asked.append(channel)
        if channel not in channels:
            raise RuntimeError("channel_not_found")
        return {"ok": True, "channel": channels[channel]}

    client = MagicMock()
    client.conversations_info = conversations_info
    assistant.app = MagicMock(client=client)  # type: ignore[assignment]
    assistant._slack_api_ready = True
    return asked


@pytest.mark.asyncio
async def test_a_star_allows_every_channel_the_bot_was_invited_to_and_nothing_else() -> None:
    assistant = _assistant()
    asked = _invited_to(
        assistant,
        {
            "C_IN": {"id": "C_IN", "is_channel": True, "is_member": True},
            "G_PRIVATE": {"id": "G_PRIVATE", "is_group": True, "is_member": True},
            "C_OUT": {"id": "C_OUT", "is_channel": True, "is_member": False},
            "G_MPIM": {"id": "G_MPIM", "is_mpim": True, "is_member": True},
        },
    )
    token = current_execution_ctx.set(_ctx(session_id="s1"))
    try:
        assistant._session_policy["s1"] = ("none", {"*"})
        assert await assistant._outbound_channel_allowed("C_IN") is True
        assert await assistant._outbound_channel_allowed("G_PRIVATE") is True
        assert await assistant._outbound_channel_allowed("C_OUT") is False
        assert await assistant._outbound_channel_allowed("G_MPIM") is False, "a group DM"
        assert await assistant._outbound_channel_allowed("C_UNKNOWN") is False
        assert await assistant._outbound_channel_allowed("D_DIRECT") is False
        assert "D_DIRECT" not in asked, "a direct message is refused without asking Slack"
        # The conversation it was invoked from stays allowed, whatever Slack says.
        assistant._session_meta["s1"] = ("C_OUT", "1.1")
        assert await assistant._outbound_channel_allowed("C_OUT") is True
    finally:
        current_execution_ctx.reset(token)


@pytest.mark.asyncio
async def test_a_mission_reports_to_an_invited_channel_under_a_star() -> None:
    assistant = _assistant()
    _invited_to(assistant, {"C_IN": {"id": "C_IN", "is_channel": True, "is_member": True}})

    assert await assistant._mission_channel_allowed(frozenset({"*"}), "C_IN") is True
    assert await assistant._mission_channel_allowed(frozenset({"*"}), "C_OTHER") is False
    assert await assistant._mission_channel_allowed(frozenset({"C1"}), "C_IN") is False
    assert await assistant._mission_channel_allowed(frozenset(), "C_ANY") is True
