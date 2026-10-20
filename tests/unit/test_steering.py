"""A message sent while a turn runs reaches the agent at its next step, without waiting for the
turn to end, and the session history keeps the order in which the agent saw it."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agent_core.orchestrator import AsyncReActOrchestrator

from specifications.interfaces.agent import SteeringMessage
from specifications.interfaces.events import EventType
from specifications.interfaces.llm import StreamChunk
from tests.turn_harness import ScriptedModel, run_turn

STEER = SteeringMessage(
    content="Also leave the docs alone", message_id="m-steer", author_user_id="u1"
)


def _steer_during_request(
    monkeypatch: pytest.MonkeyPatch, request_number: int, session_id: str
) -> None:
    """Send the steering message while the model streams its ``request_number``-th request."""
    created: list[AsyncReActOrchestrator] = []
    original_init = AsyncReActOrchestrator.__init__

    def capture(self: AsyncReActOrchestrator, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        created.append(self)

    original_stream = ScriptedModel.chat_stream

    async def stream(self: ScriptedModel, request: Any) -> AsyncIterator[StreamChunk]:
        if request.tools and sum(1 for r in self.requests if r.tools) == request_number - 1:
            assert created[-1].steer(session_id, STEER)
        async for chunk in original_stream(self, request):
            yield chunk

    monkeypatch.setattr(AsyncReActOrchestrator, "__init__", capture)
    monkeypatch.setattr(ScriptedModel, "chat_stream", stream)


_ORG = "org_steering"


async def test_a_message_sent_mid_turn_reaches_the_next_step_and_keeps_its_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _steer_during_request(monkeypatch, 2, f"session_{_ORG}")
    script = [("list_dir", {"path": "a"}), ("list_dir", {"path": "b"}), ("list_dir", {"path": "c"})]

    outcome = await run_turn(_ORG, prompt="Refactor the parser", script=script)

    react = [r for r in outcome.model.requests if r.tools]
    assert not any(STEER.content in (m.content or "") for m in react[1].messages)
    third = react[2].messages
    steered = [i for i, m in enumerate(third) if m.content == STEER.content]
    assert len(steered) == 1 and third[steered[0]].role == "user"
    assert any(m.role == "tool" for m in third[: steered[0]]), "after the earlier steps"

    history = outcome.session.conversation_history
    roles = [(m.role, m.content if m.role == "user" else None) for m in history]
    assert roles == [
        ("user", "Refactor the parser"),
        ("assistant", None),
        ("user", STEER.content),
        ("assistant", None),
    ]
    assert len(history[1].tool_calls or []) == 2 and len(history[3].tool_calls or []) == 1
    echoes = [e for e in outcome.events if getattr(e, "event_type", None) == EventType.USER_MESSAGE]
    assert any(e.payload.get("message_id") == "m-steer" for e in echoes)


def test_without_a_live_turn_nothing_is_taken() -> None:
    orchestrator = AsyncReActOrchestrator(ScriptedModel(), registry=_registry())

    assert orchestrator.steer("session_idle", STEER) is False


def _registry() -> Any:
    from agent_core.tools.registry import ToolRegistry
    from sandbox_driver.fake_driver import FakeSandboxDriver

    return ToolRegistry(driver=FakeSandboxDriver(), session_id="session_idle")


async def test_a_message_that_arrives_as_the_turn_ends_runs_as_the_next_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = "org_steering_late"
    _steer_during_request(monkeypatch, 2, f"session_{org}")

    outcome = await run_turn(org, prompt="Summarize the repo", script=[("list_dir", {"path": "."})])

    for _ in range(200):
        history = outcome.session.conversation_history
        if any(m.role == "user" and m.content == STEER.content for m in history) and (
            history[-1].role == "assistant"
        ):
            break
        await asyncio.sleep(0.01)
    users = [m.content for m in outcome.session.conversation_history if m.role == "user"]
    assert users == ["Summarize the repo", STEER.content]
    assert outcome.session.conversation_history[-1].role == "assistant"
