"""JSON-schema structured output with Pydantic validation and one repair retry.

Auxiliary calls (follow-up suggestions, session summaries, memory distillation) used to
scrape prose with regexes and backtick stripping, which is brittle across providers and
silently degrades when the phrasing shifts. Instead the caller names a Pydantic model;
the gateway constrains decoding with a JSON schema, and this helper parses and validates
the result. Exactly one repair retry is attempted — enough to recover from an
inconvenient preamble, few enough that a misbehaving provider cannot burn the budget.
"""

from __future__ import annotations

import json
from dataclasses import replace

from pydantic import BaseModel, ValidationError

from specifications.interfaces.llm import (
    ChatMessage,
    LLMGatewayProtocol,
    ModelExecutionError,
    ModelRequest,
)


class StructuredOutputError(RuntimeError):
    """Raised when a structured generation cannot be validated within its repair budget."""


async def _collect_text(gateway: LLMGatewayProtocol, request: ModelRequest) -> str:
    text = ""
    async for chunk in gateway.chat_stream(request):
        if chunk.error:
            raise ModelExecutionError(chunk.error)
        if chunk.text_delta:
            text += chunk.text_delta
        elif chunk.reasoning_delta:
            text += chunk.reasoning_delta
    return text


def extract_json(text: str) -> str:
    """Best-effort extraction of a JSON value from a model response.

    Providers that ignore ``response_format`` frequently wrap the object in a Markdown
    fence or add a sentence of preamble ("Here is the JSON…"). Stripping those before
    parsing turns a brittle exact-match into a robust one, without loosening validation —
    the extracted substring still has to be valid JSON and pass the Pydantic schema.
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        first_newline = candidate.find("\n")
        if first_newline != -1:
            candidate = candidate[first_newline + 1 :]
        if candidate.rstrip().endswith("```"):
            candidate = candidate.rstrip()[:-3]

    starts = [index for index in (candidate.find("{"), candidate.find("[")) if index != -1]
    if not starts:
        return candidate.strip()
    start = min(starts)
    end = max(candidate.rfind("}"), candidate.rfind("]"))
    if end <= start:
        return candidate.strip()
    return candidate[start : end + 1].strip()


async def generate_structured[ModelT: BaseModel](
    gateway: LLMGatewayProtocol,
    request: ModelRequest,
    schema: type[ModelT],
    *,
    max_repairs: int = 1,
) -> ModelT:
    """Generate a validated ``schema`` instance, repairing malformed JSON exactly once.

    ``request`` supplies the messages and sampling parameters; its
    ``response_schema`` is overwritten with ``schema``'s JSON schema so the provider is
    constrained regardless of what the caller passed.
    """
    messages = list(request.messages)
    attempts = max_repairs + 1
    last_error: Exception | None = None

    for attempt in range(attempts):
        constrained = replace(
            request,
            messages=messages,
            response_schema=schema.model_json_schema(),
            response_schema_name=schema.__name__,
        )
        try:
            raw = await _collect_text(gateway, constrained)
        except Exception as err:  # gateway unavailable/fatal → surface as a structured failure
            raise StructuredOutputError(f"Structured generation failed: {err}") from err

        try:
            payload = json.loads(extract_json(raw))
            return schema.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, TypeError) as err:
            last_error = err
            if attempt == attempts - 1:
                break
            messages = [
                *messages,
                ChatMessage(role="assistant", content=raw),
                ChatMessage(
                    role="user",
                    content=(
                        "Your previous response did not match the required JSON schema "
                        f"({err}). Reply with ONLY a single valid JSON object that "
                        "validates against the schema."
                    ),
                ),
            ]

    raise StructuredOutputError(
        f"Model did not return valid {schema.__name__} JSON after {max_repairs} repair retry: "
        f"{last_error}"
    )
