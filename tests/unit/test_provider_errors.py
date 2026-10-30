"""A provider that refuses the call ends the turn with an error, never with an "answer"."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import litellm
import pytest
from llm_gateway.gateway import LiteLLMGateway

from specifications.interfaces.llm import StreamChunk
from tests import turn_harness
from tests.turn_harness import run_turn


async def test_a_refused_key_is_an_error_status_and_no_stored_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = litellm.exceptions.AuthenticationError("invalid x-api-key", "anthropic", "m")

    async def refuse(self: Any, request: Any) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        for chunk in LiteLLMGateway._provider_error_chunks("anthropic/m", refused):
            yield chunk

    monkeypatch.setattr(turn_harness.ScriptedModel, "chat_stream", refuse)

    outcome = await run_turn("org_refused", prompt="hello")

    shown = [m.content or "" for m in outcome.session.conversation_history if m.role == "assistant"]
    # Shown to the person as a labelled error, never as something the model said.
    assert all(text.startswith("**The turn failed:**") for text in shown if "x-api-key" in text)
    errors = [e for e in outcome.events_of("status_changed") if e.payload.get("status") == "error"]
    assert errors and "Settings > Models" in outcome.text_of_events()
    assert not [e for e in outcome.events_of("thought") if "invalid" in str(e.payload)]
