"""Unit tests for JSON-schema structured outputs.

A scripted gateway stands in for a provider so the parse/validate/repair contract is
tested deterministically, and the deterministic stub is asserted to honour a schema so
CI exercises the constrained-decoding path end to end.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from llm_gateway.structured import StructuredOutputError, extract_json, generate_structured
from llm_gateway.stub import DeterministicStubGateway
from pydantic import BaseModel

from specifications.interfaces.llm import (
    ChatMessage,
    LLMGatewayProtocol,
    ModelRequest,
    StreamChunk,
)


class Suggestion(BaseModel):
    suggestion: str


class _ScriptedGateway(LLMGatewayProtocol):
    """Returns a fixed sequence of responses, one per call, and records the requests."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.requests: list[ModelRequest] = []

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        yield StreamChunk(text_delta=self._responses[index])
        yield StreamChunk(is_finished=True)

    async def check_budget(self, tenant_org_id: str, tenant_user_id: str) -> bool:
        return True

    async def record_usage(
        self,
        tenant_org_id: str,
        tenant_user_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        return None


def _request() -> ModelRequest:
    return ModelRequest(
        model="stub/model",
        messages=[
            ChatMessage(role="system", content="Suggest the next step."),
            ChatMessage(role="user", content="Fix the failing test."),
        ],
        stream=False,
    )


async def test_valid_json_is_returned_and_validated() -> None:
    gateway = _ScriptedGateway(['{"suggestion": "Run the auth test suite"}'])

    result = await generate_structured(gateway, _request(), Suggestion)

    assert result.suggestion == "Run the auth test suite"
    assert len(gateway.requests) == 1


async def test_schema_is_attached_to_the_request() -> None:
    gateway = _ScriptedGateway(['{"suggestion": "Run tests"}'])

    await generate_structured(gateway, _request(), Suggestion)

    sent = gateway.requests[0]
    assert sent.response_schema is not None
    assert sent.response_schema["properties"]["suggestion"]["type"] == "string"
    assert sent.response_schema_name == "Suggestion"


async def test_malformed_json_is_retried_exactly_once() -> None:
    gateway = _ScriptedGateway(["not json at all", '{"suggestion": "Run the auth test suite"}'])

    result = await generate_structured(gateway, _request(), Suggestion)

    assert result.suggestion == "Run the auth test suite"
    assert len(gateway.requests) == 2


async def test_schema_violation_is_retried_exactly_once() -> None:
    gateway = _ScriptedGateway(['{"suggestion": 12345}', '{"suggestion": "Run tests"}'])

    result = await generate_structured(gateway, _request(), Suggestion)

    assert result.suggestion == "Run tests"
    assert len(gateway.requests) == 2


async def test_persistent_failure_raises_after_one_repair() -> None:
    gateway = _ScriptedGateway(["bogus", "still bogus"])

    with pytest.raises(StructuredOutputError):
        await generate_structured(gateway, _request(), Suggestion)

    # Exactly two calls: the original plus one repair retry.
    assert len(gateway.requests) == 2


def test_extract_json_handles_fences_and_preamble() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Here is the JSON:\n{"a": 1}') == '{"a": 1}'
    assert extract_json("[1, 2, 3]") == "[1, 2, 3]"
    assert extract_json('```\n{"a": 1}\n```') == '{"a": 1}'


async def test_fenced_json_is_parsed_without_a_repair() -> None:
    # A provider that ignores response_format and wraps the object in a Markdown fence
    # must still succeed on the first attempt — this is what silently broke AI titling.
    gateway = _ScriptedGateway(['```json\n{"suggestion": "Run tests"}\n```'])

    result = await generate_structured(gateway, _request(), Suggestion)

    assert result.suggestion == "Run tests"
    assert len(gateway.requests) == 1


async def test_stub_honours_a_response_schema_deterministically() -> None:
    stub = DeterministicStubGateway()

    first = await generate_structured(stub, _request(), Suggestion)
    second = await generate_structured(stub, _request(), Suggestion)

    assert first.suggestion == second.suggestion
    assert first.suggestion  # never empty
    assert stub.call_count == 2
