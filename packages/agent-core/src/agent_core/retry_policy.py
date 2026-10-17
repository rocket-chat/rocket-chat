"""Retry policy for LLM provider errors and mid-stream disconnects (Item A4)."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Callable, Sequence
from typing import Any

from llm_gateway.errors import is_retryable_provider_error

from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.llm import LLMGatewayProtocol, ModelRequest, StreamChunk

logger = logging.getLogger(__name__)

DEFAULT_RETRY_DELAYS: tuple[float, ...] = (1.0, 4.0, 15.0)


class RetryNoticeEmitter:
    """Emits user-visible retry notices to the session event queue."""

    def __init__(
        self,
        session_id: str,
        event_queue: asyncio.Queue[AgentEvent | None] | None = None,
    ) -> None:
        self.session_id = session_id
        self.event_queue = event_queue

    async def emit_retry(
        self,
        attempt: int,
        max_attempts: int,
        delay: float,
        model: str,
        error: Exception | str,
    ) -> None:
        logger.warning(
            "Provider error on model %s (attempt %d/%d, retrying in %.1fs): %s",
            model,
            attempt,
            max_attempts,
            delay,
            error,
        )
        if self.event_queue is None:
            return
        notice = (
            f"\n\n[Provider connection disrupted ({error}). "
            f"Retrying attempt {attempt + 1}/{max_attempts} with {model} in {delay:.1f}s...]\n\n"
        )
        await self.event_queue.put(
            AgentEvent(
                event_type="status",
                session_id=self.session_id,
                payload={
                    "status": "retrying",
                    "message": f"Retrying with {model} (attempt {attempt + 1}/{max_attempts})...",
                    "discard_partial": True,
                },
            )
        )
        await self.event_queue.put(
            AgentEvent(
                event_type="thought",
                session_id=self.session_id,
                payload={"delta": notice, "is_reasoning": True},
            )
        )

    async def emit_fallback(
        self,
        failed_model: str,
        fallback_model: str,
        error: Exception | str,
    ) -> None:
        logger.warning(
            "Retries exhausted on %s (%s). Switching to fallback model %s.",
            failed_model,
            error,
            fallback_model,
        )
        if self.event_queue is None:
            return
        notice = (
            f"\n\n[Provider retries exhausted on {failed_model}. "
            f"Switching to fallback model {fallback_model}...]\n\n"
        )
        await self.event_queue.put(
            AgentEvent(
                event_type="status",
                session_id=self.session_id,
                payload={
                    "status": "retrying",
                    "message": f"Retries exhausted on {failed_model}. Switching to {fallback_model}...",
                    "discard_partial": True,
                },
            )
        )
        await self.event_queue.put(
            AgentEvent(
                event_type="thought",
                session_id=self.session_id,
                payload={"delta": notice, "is_reasoning": True},
            )
        )


async def execute_stream_with_retry[T](
    gateway: LLMGatewayProtocol,
    request: ModelRequest,
    collector_factory: Callable[[], T],
    feed_chunk: Callable[[T, StreamChunk], Any],
    session_id: str,
    event_queue: asyncio.Queue[AgentEvent | None] | None = None,
    delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
) -> tuple[T, str]:
    """Execute streaming completion with transient error retries and model fallbacks.

    Returns:
        tuple[T, str]: The successfully populated collector and the model that succeeded.
    """
    emitter = RetryNoticeEmitter(session_id, event_queue)
    candidate_models = [request.model, *(request.fallback_models or [])]
    last_err: Exception | None = None

    for model_idx, current_model in enumerate(candidate_models):
        curr_request = dataclasses.replace(request, model=current_model)
        max_attempts = len(delays) + 1

        for attempt in range(1, max_attempts + 1):
            collector = collector_factory()
            try:
                async for chunk in gateway.chat_stream(curr_request):
                    res = feed_chunk(collector, chunk)
                    if asyncio.iscoroutine(res):
                        await res
                return collector, current_model
            except Exception as err:
                last_err = err
                if not is_retryable_provider_error(err):
                    raise

                if attempt < max_attempts:
                    delay = delays[attempt - 1]
                    await emitter.emit_retry(attempt, max_attempts, delay, current_model, err)
                    await asyncio.sleep(delay)
                    continue

                if model_idx + 1 < len(candidate_models):
                    next_model = candidate_models[model_idx + 1]
                    await emitter.emit_fallback(current_model, next_model, err)
                break

    if last_err is not None:
        raise last_err
    raise RuntimeError("No candidate models to execute")
