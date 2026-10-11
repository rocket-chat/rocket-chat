"""Unit tests for the deterministic stub LLM gateway used by tests and CI."""

from __future__ import annotations

import pytest
from llm_gateway.stub import (
    QUESTION_MARKER,
    STUB_ENV_FLAG,
    TOOL_MARKER,
    DeterministicStubGateway,
    stub_enabled,
)

from specifications.interfaces.llm import (
    ChatMessage,
    LLMGatewayProtocol,
    ModelRequest,
    StreamChunk,
)


def _request(content: str, role: str = "user") -> ModelRequest:
    return ModelRequest(model="stub/model", messages=[ChatMessage(role=role, content=content)])


async def _collect(gateway: DeterministicStubGateway, request: ModelRequest) -> list[StreamChunk]:
    return [chunk async for chunk in gateway.chat_stream(request)]


def _text(chunks: list[StreamChunk]) -> str:
    return "".join(chunk.text_delta or "" for chunk in chunks)


def _reasoning(chunks: list[StreamChunk]) -> str:
    return "".join(chunk.reasoning_delta or "" for chunk in chunks)


def test_stub_satisfies_the_gateway_protocol() -> None:
    """The stub must be a drop-in for ``LLMGatewayProtocol``."""
    assert isinstance(DeterministicStubGateway(), LLMGatewayProtocol)


@pytest.mark.asyncio
async def test_streamed_output_is_a_pure_function_of_the_prompt() -> None:
    """Two identical prompts must produce byte-identical streams."""
    first = await _collect(DeterministicStubGateway(), _request("Explain the retry policy"))
    second = await _collect(DeterministicStubGateway(), _request("Explain the retry policy"))

    assert _text(first) == _text(second)
    assert _reasoning(first) == _reasoning(second)
    assert first[-1].token_usage == second[-1].token_usage


@pytest.mark.asyncio
async def test_different_prompts_produce_different_answers() -> None:
    """Distinct prompts must be distinguishable, so assertions can be specific."""
    alpha = _text(await _collect(DeterministicStubGateway(), _request("alpha task")))
    beta = _text(await _collect(DeterministicStubGateway(), _request("beta task")))

    assert alpha != beta
    assert "alpha task" in alpha
    assert "beta task" in beta


@pytest.mark.asyncio
async def test_stream_finishes_with_deterministic_usage() -> None:
    """The final chunk carries usage so downstream accounting is exercised."""
    chunks = await _collect(DeterministicStubGateway(), _request("Summarise the diff"))

    assert chunks[-1].is_finished is True
    assert chunks[-1].finish_reason == "stop"
    usage = chunks[-1].token_usage or {}
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    assert usage["total_tokens"] > 0


@pytest.mark.asyncio
async def test_tool_marker_emits_a_single_tool_call() -> None:
    """A ``[stub:tool]`` prompt exercises the tool-call streaming path."""
    chunks = await _collect(
        DeterministicStubGateway(), _request(f"{TOOL_MARKER} list the workspace")
    )

    tool_calls = [chunk.tool_call_delta for chunk in chunks if chunk.tool_call_delta]
    assert len(tool_calls) == 1
    function = tool_calls[0]["function"]
    assert isinstance(function, dict)
    assert function["name"] == "list_dir"
    assert chunks[-1].finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_question_marker_produces_a_clarifying_question() -> None:
    """A ``[stub:question]`` prompt drives the decision-gate UI deterministically."""
    text = _text(await _collect(DeterministicStubGateway(), _request(QUESTION_MARKER)))

    assert "clarifying question" in text


@pytest.mark.asyncio
async def test_answers_the_freshest_user_or_tool_input() -> None:
    """After a tool call the stub answers from the tool result, not the stale prompt."""
    request = ModelRequest(
        model="stub/model",
        messages=[
            ChatMessage(role="system", content="system preamble"),
            ChatMessage(role="user", content="first"),
            ChatMessage(role="assistant", content="calling tool"),
            ChatMessage(role="tool", content="tool result payload", tool_call_id="call_1"),
        ],
    )
    text = _text(await _collect(DeterministicStubGateway(), request))

    assert "tool result payload" in text


def test_stub_enabled_flag_parsing() -> None:
    """Only explicit truthy values may activate the stub."""
    assert stub_enabled({STUB_ENV_FLAG: "deterministic"})
    assert stub_enabled({STUB_ENV_FLAG: " TRUE "})
    assert stub_enabled({STUB_ENV_FLAG: "1"})
    assert not stub_enabled({})
    assert not stub_enabled({STUB_ENV_FLAG: "false"})
    assert not stub_enabled({STUB_ENV_FLAG: "disabled"})


def test_usage_ledger_starts_empty() -> None:
    """A fresh stub has recorded nothing."""
    assert DeterministicStubGateway().usage_ledger == []


@pytest.mark.asyncio
async def test_record_usage_feeds_the_summaries() -> None:
    """Explicitly recorded usage is reflected in the org and user summaries."""
    gateway = DeterministicStubGateway()
    await gateway.record_usage("org-1", "user-1", "stub/model", 10, 5, cache_read_tokens=3)

    assert gateway.usage_ledger[-1]["cache_read_tokens"] == 3
    org_summary = gateway.get_org_usage_summary("org-1")
    assert org_summary["total_tokens"] == 15
    assert org_summary["total_cost_usd"] == 0.0
    assert gateway.get_user_usage_summary("user-1")["total_tokens"] == 15
    assert await gateway.check_budget("org-1", "user-1") is True


def test_summary_keys_match_the_real_gateway_contract() -> None:
    """The stub must serve the same response shape as ``LiteLLMGateway``.

    The API returns these mappings straight to the client, so a stub with different keys
    would make the suite pass against a contract nothing serves — which is how a real
    ``KeyError: 'tenant_org_id'`` in production would hide behind green tests.
    """
    from llm_gateway.gateway import LiteLLMGateway

    real = LiteLLMGateway()
    stub = DeterministicStubGateway()

    assert set(stub.get_org_usage_summary("org-1")) == set(real.get_org_usage_summary("org-1"))
    assert set(stub.get_user_usage_summary("user-1")) == set(real.get_user_usage_summary("user-1"))


@pytest.mark.asyncio
async def test_chunk_streaming_is_ordered() -> None:
    """Streaming yields reasoning before text and never duplicates content."""
    chunks = await _collect(DeterministicStubGateway(), _request("ordering check"))
    kinds = [
        "reasoning" if chunk.reasoning_delta else "text"
        for chunk in chunks
        if not chunk.is_finished
    ]

    assert kinds == sorted(kinds, key=lambda kind: 0 if kind == "reasoning" else 1)
    assert len(chunks) > 3
