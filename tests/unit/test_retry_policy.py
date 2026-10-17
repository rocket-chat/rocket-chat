"""Unit tests for retry policy and mid-stream provider failure resilience (Item A4)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agent_core.orchestrator import _StreamCollector
from agent_core.retry_policy import execute_stream_with_retry
from llm_gateway.errors import is_retryable_provider_error

from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.llm import (
    ChatMessage,
    LLMGatewayProtocol,
    ModelRequest,
    StreamChunk,
)


def test_error_classification() -> None:
    # Retryable errors
    assert is_retryable_provider_error("Rate limit exceeded: 429 Too Many Requests")
    assert is_retryable_provider_error("Server overloaded: 503 Service Unavailable")
    assert is_retryable_provider_error("RemoteProtocolError: peer closed connection")
    assert is_retryable_provider_error("ConnectionResetError: connection reset by peer")
    assert is_retryable_provider_error("TimeoutError: read timeout on socket")
    assert is_retryable_provider_error(ConnectionResetError("Connection reset"))
    assert is_retryable_provider_error(TimeoutError("Socket timeout"))

    # Non-retryable / fatal errors
    assert not is_retryable_provider_error("AuthenticationError: invalid api key provided")
    assert not is_retryable_provider_error("NotFoundError: model claude-3-fake not found")
    assert not is_retryable_provider_error("BadRequestError: invalid parameter json")
    assert not is_retryable_provider_error(
        "ContextWindowExceededError: context limit 200000 exceeded"
    )


class MockDisconnectingGateway(LLMGatewayProtocol):
    """Gateway that fails mid-stream on early attempts, then succeeds."""

    def __init__(
        self, fail_attempts: int = 1, error_message: str = "Connection closed by peer"
    ) -> None:
        self.fail_attempts = fail_attempts
        self.error_message = error_message
        self.attempts = 0
        self.models_called: list[str] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.attempts += 1
        self.models_called.append(request.model)

        if self.attempts <= self.fail_attempts:
            # Emit partial text tokens before failing mid-stream
            yield StreamChunk(text_delta="Partial text before sudden disconnection...")
            raise ConnectionResetError(self.error_message)

        # Successful completion
        yield StreamChunk(text_delta="Fresh clean response after retry.")
        yield StreamChunk(
            is_finished=True,
            finish_reason="stop",
            token_usage={"prompt_tokens": 100, "completion_tokens": 20},
        )

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(self, *args: Any, **kwargs: Any) -> None:
        pass


@pytest.mark.asyncio
async def test_mid_stream_disconnect_discards_partial_and_retries_cleanly() -> None:
    gateway = MockDisconnectingGateway(fail_attempts=1)
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
    emitted_texts: list[str] = []

    async def emit_answer(session_id: str, q: Any, text: str) -> None:
        if text:
            emitted_texts.append(text)

    req = ModelRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="Hello")],
    )

    collector, final_model = await execute_stream_with_retry(
        gateway=gateway,
        request=req,
        collector_factory=lambda: _StreamCollector("s1", queue, emit_answer),
        feed_chunk=lambda c, chunk: c.feed(chunk),
        session_id="s1",
        event_queue=queue,
        delays=(0.01, 0.02),
    )

    assert gateway.attempts == 2
    assert final_model == "gpt-4o"
    # Collector only contains the text from the clean, successful run
    assert collector.text == "Fresh clean response after retry."
    assert "Partial text" not in collector.text

    # Verify retry notice was emitted into event queue
    events: list[AgentEvent] = []
    while not queue.empty():
        item = await queue.get()
        if item is not None:
            events.append(item)

    retry_notices = [
        e
        for e in events
        if e.event_type == "thought" and "Retrying attempt 2" in str(e.payload.get("delta"))
    ]
    assert len(retry_notices) == 1


@pytest.mark.asyncio
async def test_exhausted_retries_switches_to_fallback_model() -> None:
    # Fail 3 times (exhausting 3 attempts on primary model)
    gateway = MockDisconnectingGateway(fail_attempts=3)
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    req = ModelRequest(
        model="primary-model",
        fallback_models=["fallback-model"],
        messages=[ChatMessage(role="user", content="Hello")],
    )

    async def dummy_emit(*args: Any) -> None:
        pass

    collector, final_model = await execute_stream_with_retry(
        gateway=gateway,
        request=req,
        collector_factory=lambda: _StreamCollector("s1", queue, dummy_emit),
        feed_chunk=lambda c, chunk: c.feed(chunk),
        session_id="s1",
        event_queue=queue,
        delays=(0.01, 0.01),  # 2 delays = 3 attempts on primary model
    )

    assert gateway.attempts == 4  # 3 attempts on primary + 1 attempt on fallback
    assert gateway.models_called == [
        "primary-model",
        "primary-model",
        "primary-model",
        "fallback-model",
    ]
    assert final_model == "fallback-model"
    assert collector.text == "Fresh clean response after retry."

    events: list[AgentEvent] = []
    while not queue.empty():
        item = await queue.get()
        if item is not None:
            events.append(item)

    fallback_notices = [
        e
        for e in events
        if "Switching to fallback model fallback-model" in str(e.payload.get("delta"))
    ]
    assert len(fallback_notices) == 1


@pytest.mark.asyncio
async def test_fatal_error_fails_immediately_without_retrying() -> None:
    class FatalGateway(LLMGatewayProtocol):
        def __init__(self) -> None:
            self.calls = 0

        async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
            self.calls += 1
            yield StreamChunk(error="AuthenticationError: invalid api key", finish_reason="error")

        async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
            return True

        async def record_usage(self, *args: Any, **kwargs: Any) -> None:
            pass

    gateway = FatalGateway()

    req = ModelRequest(model="gpt-4o", messages=[ChatMessage(role="user", content="Hi")])

    async def dummy_emit(*args: Any) -> None:
        pass

    with pytest.raises(Exception, match="AuthenticationError"):
        await execute_stream_with_retry(
            gateway=gateway,
            request=req,
            collector_factory=lambda: _StreamCollector("s1", asyncio.Queue(), dummy_emit),
            feed_chunk=lambda c, chunk: c.feed(chunk),
            session_id="s1",
            delays=(0.01, 0.01),
        )

    assert gateway.calls == 1
