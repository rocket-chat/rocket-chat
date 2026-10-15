"""The context budget counts what a request really sends, and survives an overflow.

Tool-call arguments (a large ``file_write``), reasoning, images and the tool schemas all count;
the provider's own measurement is a floor; an organization can size models the platform does
not know; and a request the provider refuses as too large is compacted and retried once.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agent_core.context import ContextManager
from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from api.services.settings import save_org_config
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.llm import (
    CONTEXT_WINDOW_EXCEEDED,
    ChatMessage,
    LLMGatewayProtocol,
    ModelRequest,
    StreamChunk,
    ToolDefinition,
)
from tests.turn_harness import run_turn


def _write_call(index: int, size: int) -> list[ChatMessage]:
    words = " ".join(f"line{index}_{n}" for n in range(size // 8))
    arguments = json.dumps({"path": f"f{index}.txt", "content": words})
    return [
        ChatMessage(
            role="assistant",
            tool_calls=[
                {"id": f"c{index}", "function": {"name": "file_write", "arguments": arguments}}
            ],
        ),
        ChatMessage(role="tool", content="ok", tool_call_id=f"c{index}"),
    ]


def test_tool_call_arguments_and_schemas_are_counted() -> None:
    manager = ContextManager()
    plain = [ChatMessage(role="user", content="hi")]
    with_write = [*plain, *_write_call(1, 40_000)]
    tools = [ToolDefinition("big", "d" * 4000, {"type": "object"})]

    assert (
        manager.count_tokens("gpt-4o", with_write) > manager.count_tokens("gpt-4o", plain) + 5_000
    )
    assert (
        manager.count_tokens("gpt-4o", plain, tools) > manager.count_tokens("gpt-4o", plain) + 500
    )


@pytest.mark.parametrize(
    "overrides, model, expected",
    [
        ((("acme/internal-*", 32_000),), "acme/internal-7b", 32_000),
        ((("acme/internal-7b", 8_000),), "ACME/internal-7b", 8_000),
        ((), "openrouter/deepseek/deepseek-v4.1-flash", 131_072),
        ((), "someone/unknown-model", 128_000),
    ],
)
def test_the_organizations_size_wins_then_the_library_then_a_family(
    overrides: tuple[tuple[str, int], ...], model: str, expected: int
) -> None:
    assert ContextManager().get_model_context_limit(model, overrides) == expected


async def test_a_conversation_made_of_large_writes_is_compacted() -> None:
    manager = ContextManager()
    messages = [ChatMessage(role="system", content="S"), ChatMessage(role="user", content="go")]
    for index in range(12):
        messages += _write_call(index, 4_000)

    compacted = await manager.enforce_hygiene_async(
        "acme/small", messages, overrides=(("acme/small", 12_000),)
    )

    assert len(compacted) < len(messages)


async def test_the_providers_measurement_is_a_floor() -> None:
    manager = ContextManager()
    messages = [ChatMessage(role="system", content="S"), ChatMessage(role="user", content="go")]
    for index in range(6):
        messages += _write_call(index, 10)

    untouched = await manager.enforce_hygiene_async(
        "acme/m", messages, overrides=(("acme/m", 100_000),)
    )
    compacted = await manager.enforce_hygiene_async(
        "acme/m", messages, measured_tokens=95_000, overrides=(("acme/m", 100_000),)
    )

    assert untouched == messages and len(compacted) < len(messages)


class _OverflowingModel(LLMGatewayProtocol):
    """Refuses requests above ``limit`` messages as too large, like a provider would."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        if len(request.messages) > self.limit:
            yield StreamChunk(
                is_finished=True,
                finish_reason=CONTEXT_WINDOW_EXCEEDED,
                error="This model's maximum context length is exceeded",
            )
            return
        yield StreamChunk(text_delta="answer after compaction")

    async def check_budget(self, *args: Any) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


async def _turn(orchestrator: AsyncReActOrchestrator, session: str) -> list[str]:
    seen: list[str] = []
    async for event in orchestrator.process_user_turn(session, "continue", max_turns=3):
        if event.event_type == "thought" and not event.payload.get("is_reasoning"):
            seen.append(event.payload["delta"])
        if event.event_type == "status" and event.payload.get("state") == "error":
            seen.append("ERROR")
    return seen


async def test_a_request_refused_as_too_large_is_compacted_and_retried_once() -> None:
    model = _OverflowingModel(limit=12)
    orchestrator = AsyncReActOrchestrator(
        model, ToolRegistry(driver=FakeSandboxDriver(), session_id="s_over")
    )
    history = orchestrator._session_histories.setdefault("s_over", [])
    history.append(ChatMessage(role="user", content="start"))
    for index in range(10):
        history += _write_call(index, 50)

    output = "".join(await _turn(orchestrator, "s_over"))

    react = [r for r in model.requests if r.tools]
    assert "answer after compaction" in output
    assert len(react) == 2 and len(react[1].messages) <= 12, "one retry, on the compacted history"


async def test_a_second_refusal_ends_the_turn_with_an_error() -> None:
    model = _OverflowingModel(limit=1)
    orchestrator = AsyncReActOrchestrator(
        model, ToolRegistry(driver=FakeSandboxDriver(), session_id="s_over2")
    )
    history = orchestrator._session_histories.setdefault("s_over2", [])
    history.append(ChatMessage(role="user", content="start"))
    for index in range(10):
        history += _write_call(index, 50)

    assert "ERROR" in await _turn(orchestrator, "s_over2")
    assert len([r for r in model.requests if r.tools]) == 2, "no second retry"


@pytest.mark.setting("models.context_windows")
async def test_an_organizations_context_window_decides_when_a_turn_compacts() -> None:
    org = "wire_context_windows"
    await save_org_config(None, org, "models", {"context_windows": {"*": 2_000}}, [])
    script = [("list_dir", {"path": "."})] * 6

    outcome = await run_turn(org, script=script)

    compacted = [
        r
        for r in outcome.model.requests
        if any("Compacted" in (m.content or "") for m in r.messages)
    ]
    assert compacted, "a 2,000-token window forces compaction during the turn"
    unbounded = await run_turn("wire_context_windows_default", script=script)
    assert not any(
        "Compacted" in (m.content or "") for r in unbounded.model.requests for m in r.messages
    )
