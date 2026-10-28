"""Engine notices to the model are per request, never stored, and never ``system`` mid-history.

Several provider translations (LiteLLM's for Anthropic- and Gemini-style APIs) move every system
message into the system prompt. A budget or "do not call tools" notice stored as one used to
become a standing rule for every later turn of the session.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest
from agent_core.notes import REMINDER_OPEN, with_turn_notes
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.services.transcript import InMemoryTranscriptStore
from llm_gateway.gateway import LiteLLMGateway, misplaced_system_messages
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import (
    ChatMessage,
    LLMGatewayProtocol,
    ModelRequest,
    StreamChunk,
)
from tests.unit.test_turn_budget_synthesis import _TOOL_CALL

CHECKLIST_CALL = {
    "index": 0,
    "id": "call_plan",
    "type": "function",
    "function": {
        "name": "update_task_checklist",
        "arguments": '{"tasks": [{"id": "1", "title": "Do it", "status": "in_progress"}]}',
    },
}
CONTROL_PHRASES = ("Budget notice", "maximum allowed turn limit", "Do NOT", "open tasks")


class _ScriptedGateway(LLMGatewayProtocol):
    """Tool calls until ``tool_steps`` are used, then answers; records every request."""

    def __init__(
        self,
        tool_steps: int,
        first_call: dict[str, object] | None = None,
        calls: list[dict[str, object]] | None = None,
    ) -> None:
        self.tool_steps = tool_steps
        self.calls = list(calls) if calls is not None else ([first_call] if first_call else [])
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        if request.tool_choice != "none" and self.tool_steps > 0:
            self.tool_steps -= 1
            call = self.calls.pop(0) if self.calls else _TOOL_CALL
            yield StreamChunk(tool_call_delta=call)
            return
        yield StreamChunk(text_delta="ANSWER")

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: object, **kwargs: object) -> None:
        return None


async def _turn(orchestrator: AsyncReActOrchestrator, session_id: str, limit: int) -> None:
    async for _ in orchestrator.process_user_turn(session_id, "do the task", max_turns=limit):
        pass


def _texts(request: ModelRequest) -> str:
    return "\n".join(m.content or "" for m in request.messages)


@pytest.mark.asyncio
async def test_a_spent_budget_does_not_follow_the_session_into_the_next_turn() -> None:
    transcripts = InMemoryTranscriptStore()
    gateway = _ScriptedGateway(tool_steps=99)
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s_budget")
    orchestrator = AsyncReActOrchestrator(gateway, registry, transcript_store=transcripts)

    await _turn(orchestrator, "s_budget", limit=3)
    assert any("maximum allowed turn limit" in _texts(r) for r in gateway.requests)
    gateway.tool_steps, gateway.requests = 0, []
    await _turn(orchestrator, "s_budget", limit=3)

    next_turn = _texts(gateway.requests[0])
    assert not any(phrase in next_turn for phrase in CONTROL_PHRASES), next_turn
    stored = await transcripts.load("s_budget", "default_org")
    assert all(m.role != "system" for m in stored)
    assert not any(REMINDER_OPEN in (m.content or "") for m in stored)


@pytest.mark.asyncio
async def test_every_request_has_only_leading_system_messages() -> None:
    calls = [
        {
            "index": 0,
            "id": "c1",
            "type": "function",
            "function": {
                "name": "file_write",
                "arguments": '{"path": "foo.py", "content": "x=1"}',
            },
        },
        {
            "index": 0,
            "id": "c2",
            "type": "function",
            "function": {
                "name": "update_task_checklist",
                "arguments": '{"tasks": [{"id": "1", "title": "Do it", "status": "completed"}]}',
            },
        },
    ]
    gateway = _ScriptedGateway(tool_steps=2, calls=calls)
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s_lead")
    orchestrator = AsyncReActOrchestrator(gateway, registry)

    await _turn(orchestrator, "s_lead", limit=10)

    assert len(gateway.requests) >= 3, (
        "tool step 1, tool step 2, answer, verification reminder, answer"
    )
    assert all(misplaced_system_messages(r.messages) == 0 for r in gateway.requests)
    reminded = [
        r for r in gateway.requests if "Verification gate" in (r.messages[-1].content or "")
    ]
    assert len(reminded) == 1 and reminded[0].messages[-1].role == "user"


@pytest.mark.asyncio
async def test_a_legacy_transcript_with_system_notices_heals_on_rehydration() -> None:
    transcripts = InMemoryTranscriptStore()
    await transcripts.append(
        "s_legacy",
        "default_org",
        [
            ChatMessage(role="user", content="first"),
            ChatMessage(role="system", content="The maximum allowed turn limit (3 turns)..."),
            ChatMessage(role="assistant", content="answer"),
        ],
    )
    gateway = _ScriptedGateway(tool_steps=0)
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s_legacy")
    orchestrator = AsyncReActOrchestrator(gateway, registry, transcript_store=transcripts)

    await _turn(orchestrator, "s_legacy", limit=3)

    assert "maximum allowed turn limit" not in _texts(gateway.requests[0])
    assert misplaced_system_messages(gateway.requests[0].messages) == 0


def test_notes_join_the_last_user_message_or_follow_tool_results() -> None:
    user_last = [ChatMessage(role="user", content="hi")]
    merged = with_turn_notes(user_last, ["Be brief."])
    assert len(merged) == 1 and merged[0].content.startswith("hi\n\n<system-reminder>")
    assert user_last[0].content == "hi", "the stored message is untouched"

    after_tool = [
        ChatMessage(role="assistant", tool_calls=[{"id": "c", "function": {"name": "x"}}]),
        ChatMessage(role="tool", content="out", tool_call_id="c"),
    ]
    appended = with_turn_notes(after_tool, ["Be brief."])
    assert appended[-1].role == "user" and "Be brief." in (appended[-1].content or "")
    assert with_turn_notes(after_tool, []) is after_tool


@pytest.mark.parametrize(
    "model",
    [
        "anthropic/claude-sonnet-4-5",
        "openai/gpt-5",
        "deepseek/deepseek-chat",
        "gemini/gemini-2.5-pro",
        "openrouter/deepseek/deepseek-v4.1-flash",
    ],
)
def test_the_gateway_warns_about_a_misplaced_system_message(
    model: str, caplog: pytest.LogCaptureFixture
) -> None:
    gateway = LiteLLMGateway()
    clean = [ChatMessage(role="system", content="S"), ChatMessage(role="user", content="u")]
    with caplog.at_level(logging.WARNING, logger="llm_gateway.gateway"):
        gateway._format_request_messages(clean, model)
    assert not caplog.records
    with caplog.at_level(logging.WARNING, logger="llm_gateway.gateway"):
        gateway._format_request_messages([*clean, ChatMessage(role="system", content="x")], model)
    assert "1 system message(s)" in caplog.text
