"""How many tokens a request sends, when the provider has not said.

One count for every caller: the context manager deciding when to compact, and the usage
ledger when a provider streams no usage block.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import litellm

from llm_gateway.reasoning import _BLOCKS, _CONTENT, reasoning_replay
from specifications.interfaces.llm import ChatMessage, ToolDefinition

# An estimated call is never free: providers bill framing tokens around every request and
# reply, and a ledger entry of zero would read as a call that cost nothing.
MIN_ESTIMATED_PROMPT_TOKENS = 50
MIN_ESTIMATED_COMPLETION_TOKENS = 10
# What one image costs a model, roughly: a request with screenshots must not look empty.
IMAGE_TOKEN_ESTIMATE = 1_600


def request_tokens(
    model: str, messages: Sequence[ChatMessage], tools: Sequence[ToolDefinition] | None = ()
) -> int:
    """Everything the request sends: text, tool calls and their arguments, reasoning, images
    and the tool schemas (a large ``file_write`` lives in arguments, not in content)."""
    replay = reasoning_replay(model)
    parts: list[str] = []
    images = 0
    for message in messages:
        parts.append(message.content or "")
        if message.tool_calls:
            parts.append(json.dumps(message.tool_calls))
        # Only count reasoning tokens if the model family re-sends reasoning blocks on the wire.
        # Otherwise, historical reasoning is not sent and counting it inflates context estimation.
        if message.reasoning and replay in (_BLOCKS, _CONTENT):
            parts.append(message.reasoning)
        images += sum(1 for item in message.attachments or [] if item.is_image)
    for tool in tools or ():
        parts.append(f"{tool.name} {tool.description} {json.dumps(tool.parameters_json_schema)}")
    return text_tokens(model, "\n".join(parts)) + images * IMAGE_TOKEN_ESTIMATE


def text_tokens(model: str, text: str) -> int:
    """Tokens in ``text`` with the model's tokenizer, else about four characters a token."""
    if not text:
        return 0
    try:
        return int(litellm.token_counter(model=model, text=text))
    except Exception:
        return max(1, len(text) // 4)


def estimated_usage(
    model: str,
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolDefinition] | None,
    produced: str,
) -> tuple[int, int]:
    """(prompt, completion) tokens to bill when the provider reported no usage."""
    return (
        max(MIN_ESTIMATED_PROMPT_TOKENS, request_tokens(model, messages, tools)),
        max(MIN_ESTIMATED_COMPLETION_TOKENS, text_tokens(model, produced)),
    )
