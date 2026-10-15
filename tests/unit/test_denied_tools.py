"""A caller allowed every tool keeps the turn's MCP tools; its denied tools stay out.

Slack used to turn "*" into a list of the tools registered before the turn, which dropped
every MCP tool (they only exist once the turn's connectors are attached).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.services.transcript import InMemoryTranscriptStore

from specifications.interfaces.agent import TurnCallbacks
from specifications.interfaces.llm import ModelRequest, StreamChunk, ToolDefinition

MCP_TOOL = ToolDefinition(
    name="mcp__signoz__search_logs", description="Search logs.", parameters_json_schema={}
)


class _SigNoz:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def definitions(self) -> list[ToolDefinition]:
        return [MCP_TOOL]

    def owns(self, name: str) -> bool:
        return name == MCP_TOOL.name

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append(name)
        return "3 start_apply lines"


class _Model:
    """Calls the MCP tool, then the denied tool, then answers."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        step = sum(1 for m in request.messages if m.role == "tool")
        if request.tools and step < 2:
            name = [MCP_TOOL.name, "git_push"][step]
            yield StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"c{step}",
                    "function": {"name": name, "arguments": "{}"},
                }
            )
            return
        yield StreamChunk(text_delta="Found them.")

    async def check_budget(self, *_: Any) -> bool:
        return True

    async def record_usage(self, **_: Any) -> None:
        return None


async def test_every_tool_means_the_turns_mcp_tools_too_and_denied_ones_stay_out() -> None:
    model, signoz = _Model(), _SigNoz()
    orchestrator = AsyncReActOrchestrator(
        gateway=model,  # type: ignore[arg-type]
        registry=ToolRegistry(),
        transcript_store=InMemoryTranscriptStore(),
    )

    events = [
        e
        async for e in orchestrator.process_user_turn(
            "s_denied",
            "find start_apply logs",
            whitelisted_tools=None,
            denied_tools=frozenset({"git_push"}),
            callbacks=TurnCallbacks(external_tools=signoz),
        )
    ]

    offered = {t.name for t in model.requests[0].tools or []}
    assert MCP_TOOL.name in offered and "git_push" not in offered
    assert signoz.calls == [MCP_TOOL.name]
    results = [str(e.payload.get("result")) for e in events if e.event_type == "tool_completed"]
    assert any("git_push" in r and "not permitted" in r for r in results), results
