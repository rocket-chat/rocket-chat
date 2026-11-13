"""A model-free scope (the sandbox MCP) is refused at every place that calls a provider."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from api.services.knowledge import compute_embedding
from llm_gateway.gateway import LiteLLMGateway

from specifications.interfaces.llm import (
    MODEL_FREE_CALL_ROLE,
    ChatMessage,
    ModelCallForbidden,
    ModelRequest,
    ModelScope,
    current_model_scope,
)


def _model_free() -> ModelScope:
    return ModelScope(org_id="org", user_id="alice", call_role=MODEL_FREE_CALL_ROLE)


async def test_the_gateway_refuses_a_completion_before_reaching_the_provider() -> None:
    gateway = LiteLLMGateway()
    request = ModelRequest(model="openai/gpt-4o-mini", messages=[ChatMessage("user", "hi")])
    token = current_model_scope.set(_model_free())
    try:
        with patch("llm_gateway.gateway.litellm.acompletion", new=AsyncMock()) as provider:
            with pytest.raises(ModelCallForbidden):
                async for _ in gateway.chat_stream(request):
                    pass
    finally:
        current_model_scope.reset(token)
    provider.assert_not_awaited()


async def test_an_embedding_is_refused_instead_of_falling_back_silently() -> None:
    token = current_model_scope.set(_model_free())
    try:
        with patch("litellm.aembedding", new=AsyncMock()) as provider:
            with pytest.raises(ModelCallForbidden):
                await compute_embedding("build command")
    finally:
        current_model_scope.reset(token)
    provider.assert_not_awaited()


async def test_any_other_scope_still_reaches_the_provider() -> None:
    token = current_model_scope.set(ModelScope(org_id="org", user_id="alice", call_role="react"))
    try:
        with patch("litellm.aembedding", new=AsyncMock(return_value=None)) as provider:
            await compute_embedding("build command")
    finally:
        current_model_scope.reset(token)
    provider.assert_awaited_once()
