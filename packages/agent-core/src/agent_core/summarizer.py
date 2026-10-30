"""The model call behind a compaction's summary, shared by the main agent and its delegates.

With ``tools`` (the turn's own model and tool set), the summary is asked on the live request: the
same system prompt, tools and history, so the provider serves almost all of it from its cache and
the model reads its real tool calls rather than a flattened copy. ``tool_choice: none`` and
reasoning off keep the answer to text: a reasoning model given a small output budget had spent it
thinking and returned nothing, and a model shown an agentic conversation had answered with a tool
call. A request the provider refuses (too large, or no ``tool_choice`` beside tools) is asked
again as one flattened transcript, which is also the form for a summary model other than the
turn's, since that model has no cache of the conversation.
"""

from __future__ import annotations

import logging

from agent_core.context import Summarizer, render_transcript
from specifications.interfaces.llm import (
    ChatMessage,
    LLMGatewayProtocol,
    ModelExecutionError,
    ModelRequest,
    ToolDefinition,
)

logger = logging.getLogger(__name__)

# Room for the summary even when a provider counts reasoning against the output budget.
SUMMARY_MAX_TOKENS = 12_000
FLATTENED_SUMMARY_SYSTEM = (
    "You write checkpoints of long coding-agent sessions so the work can continue. Answer with "
    "text only."
)


def flattened_summary_request(request: list[ChatMessage]) -> list[ChatMessage]:
    """``request`` (a conversation, then the summary prompt) as one transcript to summarise."""
    *conversation, prompt = request
    transcript = render_transcript([m for m in conversation if m.role != "system"])
    return [
        ChatMessage(role="system", content=FLATTENED_SUMMARY_SYSTEM),
        ChatMessage(role="user", content=f"{transcript}\n\n{prompt.content or ''}"),
    ]


async def _summary_call(
    gateway: LLMGatewayProtocol,
    model: str,
    messages: list[ChatMessage],
    tools: list[ToolDefinition],
) -> str:
    request = ModelRequest(
        model=model,
        messages=messages,
        tools=tools or None,
        tool_choice="none" if tools else None,
        # Only OpenRouter takes "none" as an effort; elsewhere no effort is no reasoning request.
        reasoning_effort="none" if model.startswith("openrouter/") else None,
        thinking_budget_tokens=None,
        max_tokens=SUMMARY_MAX_TOKENS,
        temperature=0.1,
        stream=False,
    )
    text = ""
    async for chunk in gateway.chat_stream(request):
        if chunk.error:
            raise ModelExecutionError(chunk.error, chunk.refusal)
        if chunk.text_delta:
            text += chunk.text_delta
    return text


def model_summarizer(
    gateway: LLMGatewayProtocol, model: str, tools: list[ToolDefinition] | None = None
) -> Summarizer:
    """A compaction summarizer on ``model``; on the live request when ``tools`` are given."""

    async def summarize(request: list[ChatMessage]) -> str:
        if tools:
            try:
                return await _summary_call(gateway, model, request, tools)
            except Exception as err:
                logger.warning("Summary on the live request failed, flattening it: %s", err)
        return await _summary_call(gateway, model, flattened_summary_request(request), [])

    return summarize
