"""Usage is billed from what the provider reports, and estimated from the whole request when
it reports nothing: tool schemas and tool-call arguments are tokens too."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import litellm
import pytest
from llm_gateway.gateway import LiteLLMGateway, _usage_options
from llm_gateway.tokens import estimated_usage, request_tokens, text_tokens

from specifications.interfaces.llm import (
    ChatMessage,
    ModelRequest,
    ModelScope,
    ToolDefinition,
    UsageEvent,
    current_model_scope,
)
from tests.turn_harness import ScriptedModel, run_turn

BIG_SCHEMA = {"type": "object", "properties": {f"field_{n}": {"type": "string"} for n in range(80)}}
TOOL = ToolDefinition(
    name="file_write", description="Write a file.", parameters_json_schema=BIG_SCHEMA
)


@pytest.mark.parametrize(
    "model, asks",
    [
        ("openai/gpt-4o", True),
        ("azure/gpt-4o", True),
        ("openrouter/deepseek/deepseek-chat", True),
        ("deepseek/deepseek-chat", True),
        ("hosted_vllm/my-model", True),
        ("anthropic/claude-3-5-sonnet-20241022", False),
        ("gemini/gemini-1.5-pro", False),
        ("ollama/llama3", False),
        ("not-a-provider/whatever", False),
    ],
)
def test_usage_is_requested_only_where_the_provider_takes_the_option(
    model: str, asks: bool
) -> None:
    expected = {"stream_options": {"include_usage": True}} if asks else {}

    assert _usage_options(model) == expected
    kwargs = LiteLLMGateway()._completion_kwargs(
        model, ModelRequest(model=model, messages=[ChatMessage(role="user", content="hi")])
    )
    assert ("stream_options" in kwargs) is asks


def _chunk(content: str | None, finish: str | None, usage: Any = None, tool_args: str = "") -> Any:
    calls = (
        [SimpleNamespace(index=0, id="c1", type="function",
                         function=SimpleNamespace(name="file_write", arguments=tool_args))]
        if tool_args else None
    )  # fmt: skip
    delta = SimpleNamespace(
        content=content, reasoning_content=None, thinking=None, thinking_blocks=None,
        tool_calls=calls,
    )  # fmt: skip
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=usage
    )


class _Ledger:
    def __init__(self) -> None:
        self.events: list[UsageEvent] = []

    async def record(self, event: UsageEvent) -> None:
        self.events.append(event)


async def _billed(
    monkeypatch: pytest.MonkeyPatch, chunks: list[Any]
) -> tuple[UsageEvent, list[Any]]:
    async def acompletion(**kwargs: Any) -> AsyncIterator[Any]:
        async def stream() -> AsyncIterator[Any]:
            for chunk in chunks:
                yield chunk

        return stream()

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    gateway, ledger = LiteLLMGateway(), _Ledger()
    gateway.usage_sink = ledger
    token = current_model_scope.set(ModelScope(org_id="o", user_id="u", session_id="s"))
    try:
        request = ModelRequest(
            model="openai/gpt-4o", messages=[ChatMessage(role="user", content="hi")], tools=[TOOL]
        )
        seen = [c async for c in gateway.chat_stream(request)]
    finally:
        current_model_scope.reset(token)
    return ledger.events[0], seen


async def test_usage_on_a_content_chunk_is_not_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = SimpleNamespace(prompt_tokens=1234, completion_tokens=56, total_tokens=1290,
                            cache_read_input_tokens=None, cache_creation_input_tokens=None,
                            prompt_tokens_details=None)  # fmt: skip

    event, seen = await _billed(monkeypatch, [_chunk("Hel", None, usage), _chunk("lo", "stop")])

    assert (event.prompt_tokens, event.completion_tokens) == (1234, 56)
    assert seen[-1].token_usage["prompt_tokens"] == 1234


async def test_without_usage_the_estimate_counts_schemas_and_tool_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = json.dumps({"path": "a.py", "content": "x = 1\n" * 400})

    event, _ = await _billed(monkeypatch, [_chunk(None, "tool_calls", tool_args=arguments)])

    request_only_text = len("hi") // 4
    assert event.prompt_tokens > 300 > request_only_text, "the tool schema is sent and billed"
    assert event.completion_tokens == text_tokens("openai/gpt-4o", arguments)
    assert event.prompt_tokens == request_tokens(
        "openai/gpt-4o", [ChatMessage(role="user", content="hi")], [TOOL]
    )


async def test_a_turn_on_a_gateway_without_usage_bills_the_whole_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    billed: list[dict[str, Any]] = []

    async def record_usage(self: ScriptedModel, **kwargs: Any) -> None:
        billed.append(kwargs)

    monkeypatch.setattr(ScriptedModel, "record_usage", record_usage)
    content = "x = 1\n" * 300

    outcome = await run_turn(
        "org_usage", script=[("file_write", {"path": "a.py", "content": content})]
    )

    first_request = outcome.model.requests[0]
    written = json.dumps({"path": "a.py", "content": content})
    assert (billed[0]["prompt_tokens"], billed[0]["completion_tokens"]) == estimated_usage(
        billed[0]["model"], first_request.messages, first_request.tools, written
    )
    assert billed[0]["completion_tokens"] > 300, "the written file is output"


def test_an_estimated_call_is_never_free() -> None:
    assert estimated_usage("openai/gpt-4o", [ChatMessage(role="user", content="hi")], None, "") == (
        50,
        10,
    )
