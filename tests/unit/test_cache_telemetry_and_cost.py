"""Tests for B2 (cache telemetry, cache-aware cost, provider stickiness) and B9 (tool ordering)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.services.turn_trace import TurnTraceAccumulator
from llm_gateway.gateway import LiteLLMGateway
from llm_gateway.pricing import estimate_cost_usd
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.agent import TurnCallbacks
from specifications.interfaces.llm import (
    ChatMessage,
    ModelRequest,
    ModelScope,
    ToolDefinition,
    current_model_scope,
)


def test_usage_parsing_openai_object() -> None:
    chunk = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            total_tokens=1200,
            prompt_tokens_details=SimpleNamespace(cached_tokens=800),
        )
    )
    parsed = LiteLLMGateway._usage_from_chunk(chunk)
    assert parsed is not None
    assert parsed["prompt_tokens"] == 1000
    assert parsed["completion_tokens"] == 200
    assert parsed["total_tokens"] == 1200
    assert parsed["cache_read_tokens"] == 800


def test_usage_parsing_openai_dict() -> None:
    chunk = {
        "usage": {
            "prompt_tokens": 1500,
            "completion_tokens": 100,
            "total_tokens": 1600,
            "prompt_tokens_details": {"cached_tokens": 1200},
        }
    }
    parsed = LiteLLMGateway._usage_from_chunk(chunk)
    assert parsed is not None
    assert parsed["prompt_tokens"] == 1500
    assert parsed["cache_read_tokens"] == 1200


def test_usage_parsing_anthropic_format() -> None:
    chunk = SimpleNamespace(
        usage={
            "prompt_tokens": 2000,
            "completion_tokens": 300,
            "total_tokens": 2300,
            "cache_read_input_tokens": 1600,
            "cache_creation_input_tokens": 400,
        }
    )
    parsed = LiteLLMGateway._usage_from_chunk(chunk)
    assert parsed is not None
    assert parsed["prompt_tokens"] == 2000
    assert parsed["completion_tokens"] == 300
    assert parsed["cache_read_tokens"] == 1600
    assert parsed["cache_creation_tokens"] == 400


def test_usage_parsing_openrouter_cached_tokens() -> None:
    chunk = {
        "usage": {
            "prompt_tokens": 800,
            "completion_tokens": 50,
            "total_tokens": 850,
            "cached_tokens": 600,
        }
    }
    parsed = LiteLLMGateway._usage_from_chunk(chunk)
    assert parsed is not None
    assert parsed["prompt_tokens"] == 800
    assert parsed["cache_read_tokens"] == 600


def test_cost_calculation_applies_cache_discounts() -> None:
    # Full prompt uncached vs cached on Claude Sonnet
    model = "anthropic/claude-sonnet-4-5"
    full_cost = estimate_cost_usd(model, prompt_tokens=100_000, completion_tokens=0)
    cached_cost = estimate_cost_usd(
        model,
        prompt_tokens=100_000,
        completion_tokens=0,
        cache_read_tokens=100_000,
    )
    # Claude gives a 90% discount on cache read tokens
    assert cached_cost < full_cost
    assert round(cached_cost / full_cost, 2) == 0.10

    # DeepSeek cached reads also receive a discount
    ds_model = "deepseek/deepseek-chat"
    ds_full = estimate_cost_usd(ds_model, prompt_tokens=100_000, completion_tokens=0)
    ds_cached = estimate_cost_usd(
        ds_model,
        prompt_tokens=100_000,
        completion_tokens=0,
        cache_read_tokens=100_000,
    )
    assert ds_cached < ds_full


def test_completion_kwargs_carries_sticky_session_routing() -> None:
    gateway = LiteLLMGateway()
    request = ModelRequest(
        model="openrouter/deepseek/deepseek-chat",
        messages=[ChatMessage(role="user", content="hello")],
    )

    token = current_model_scope.set(
        ModelScope(org_id="org_test", user_id="user_test", session_id="session_xyz_123")
    )
    try:
        kwargs = gateway._completion_kwargs("openrouter/deepseek/deepseek-chat", request)
        assert kwargs.get("user") == "session_xyz_123"
        assert kwargs.get("prompt_cache_key") == "session_xyz_123"
    finally:
        current_model_scope.reset(token)


class _DummyMcpProvider:
    def __init__(self, names: list[str]) -> None:
        self.names = names

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=name,
                description=f"Desc for {name}",
                parameters_json_schema={"type": "object", "properties": {}},
            )
            for name in self.names
        ]

    def owns(self, name: str) -> bool:
        return name in self.names

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        return f"{name} done"


@pytest.mark.asyncio
async def test_b9_property_serialized_tools_array_monotonic_append() -> None:
    """Property test B9: serialised tools array of request k is request k-1 plus appended entries only."""
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s_b9")
    mcp_names = [
        "mcp__zeta__tool2",
        "mcp__alpha__tool1",
        "mcp__alpha__tool2",
        "mcp__beta__tool1",
    ]
    provider = _DummyMcpProvider(mcp_names)

    ctx = ExecutionContext(
        session_id="s_b9",
        event_queue=asyncio.Queue(),
        parent_model="m",
        callbacks=TurnCallbacks(external_tools=provider),  # type: ignore[arg-type]
    )
    token = current_execution_ctx.set(ctx)

    try:
        # Step 0: Base tools: built-ins, dynamic tools, MCP sorted by server then name
        defs = registry.get_tool_definitions()
        ext_defs = [d.name for d in defs if d.name.startswith("mcp__")]
        assert ext_defs == [
            "mcp__alpha__tool1",
            "mcp__alpha__tool2",
            "mcp__beta__tool1",
            "mcp__zeta__tool2",
        ], "MCP tools are sorted by server then name"

        # Now simulate deferred tools: defer beta and zeta tools
        orchestrator = AsyncReActOrchestrator(gateway=LiteLLMGateway(), registry=registry)
        ctx.deferred_tools = frozenset({"mcp__beta__tool1", "mcp__zeta__tool2"})
        ctx.loaded_tools = set()
        ctx.loaded_tools_order = []

        # Request 1 (before load)
        tools_req1 = [t.name for t in orchestrator._tools_for_request(None)]
        assert "mcp__beta__tool1" not in tools_req1
        assert "mcp__zeta__tool2" not in tools_req1

        # Agent loads mcp__zeta__tool2
        await registry.load(["mcp__zeta__tool2"])

        # Request 2 (after loading zeta)
        tools_req2 = [t.name for t in orchestrator._tools_for_request(None)]
        # Prefix must be byte-identical to Request 1
        assert tools_req2[: len(tools_req1)] == tools_req1
        assert tools_req2[len(tools_req1) :] == ["mcp__zeta__tool2"]

        # Agent loads mcp__beta__tool1
        await registry.load(["mcp__beta__tool1"])

        # Request 3 (after loading beta)
        tools_req3 = [t.name for t in orchestrator._tools_for_request(None)]
        # Prefix must be byte-identical to Request 2
        assert tools_req3[: len(tools_req2)] == tools_req2
        assert tools_req3[len(tools_req2) :] == ["mcp__beta__tool1"]

    finally:
        current_execution_ctx.reset(token)


def test_turn_trace_records_cache_read_and_creation_by_model() -> None:
    trace = TurnTraceAccumulator(session_id="s", org_id="o", turn_index=1)
    trace.record_model_usage(
        {
            "model": "anthropic/claude-sonnet-4-5",
            "prompt_tokens": 1000,
            "completion_tokens": 150,
            "cache_read_tokens": 800,
            "cache_creation_tokens": 100,
        }
    )
    record = trace.finish(status="completed")
    usage = record.tokens["usage"]
    assert usage["cache_read_tokens"] == 800
    assert usage["cache_creation_tokens"] == 100
    assert usage["cache_hit_ratio"] == 0.8

    by_model = record.tokens["by_model"]["anthropic/claude-sonnet-4-5"]
    assert by_model["cache_read_tokens"] == 800
    assert by_model["cache_creation_tokens"] == 100
