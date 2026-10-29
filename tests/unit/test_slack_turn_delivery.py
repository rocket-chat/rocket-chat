"""What Slack posts is the turn's answer: not its narration, and not another turn's."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

from slack_harness import make_slack

from specifications.interfaces.agent import AgentEvent, AgentOrchestratorProtocol


def _text(session_id: str, text: str) -> AgentEvent:
    return AgentEvent("thought", session_id, {"delta": text, "is_reasoning": False})


def _tool(session_id: str) -> AgentEvent:
    return AgentEvent(
        "tool_call", session_id, {"id": "c1", "name": "file_read", "arguments": {"path": "a"}}
    )


class _ScriptedOrchestrator(AgentOrchestratorProtocol):
    """Answers each prompt with its script; a prompt in ``hold`` waits until released."""

    def __init__(self, scripts: dict[str, list[str | None]]) -> None:
        self.scripts = scripts
        self.hold: dict[str, asyncio.Event] = {}
        self.started: dict[str, asyncio.Event] = {p: asyncio.Event() for p in scripts}

    def process_user_turn(
        self, session_id: str, user_message: str, *args: Any, **kwargs: Any
    ) -> AsyncIterator[AgentEvent]:
        async def _events() -> AsyncIterator[AgentEvent]:
            self.started[user_message].set()
            for step in self.scripts[user_message]:
                if step is None:
                    yield _tool(session_id)
                    continue
                yield _text(session_id, step)
                if user_message in self.hold:
                    await self.hold[user_message].wait()

        return _events()

    async def submit_human_approval(self, session_id: str, action_id: str, approved: bool) -> None:
        return None

    async def submit_question_answer(
        self,
        session_id: str,
        question_id: str,
        selected_options: list[str],
        custom_text: str | None = None,
    ) -> None:
        return None

    async def cancel_turn(self, session_id: str) -> None:
        return None


def _posted(say: AsyncMock) -> list[str]:
    return [str(call.kwargs.get("text", "")) for call in say.call_args_list]


async def test_the_narration_before_a_tool_call_is_not_part_of_the_answer() -> None:
    orchestrator = _ScriptedOrchestrator(
        {"go": ["Let me look at the file first.", None, "The answer is 42."]}
    )
    assistant = make_slack(orchestrator)
    say = AsyncMock()

    await assistant.process_incoming_request(
        channel_id="C_NARR", thread_ts="1.0", raw_text="go", say_fn=say
    )

    posted = "\n".join(_posted(say))
    assert "The answer is 42." in posted
    assert "Let me look" not in posted


async def test_two_messages_in_a_thread_get_their_own_answers_in_order() -> None:
    orchestrator = _ScriptedOrchestrator(
        {"first": ["Answer to the first."], "second": ["Answer to the second."]}
    )
    orchestrator.hold["first"] = asyncio.Event()
    assistant = make_slack(orchestrator)
    say = AsyncMock()

    def message(text: str) -> asyncio.Task[None]:
        return asyncio.create_task(
            assistant.process_incoming_request(
                channel_id="C_TWO", thread_ts="2.0", raw_text=text, say_fn=say
            )
        )

    first = message("first")
    await asyncio.wait_for(orchestrator.started["first"].wait(), 5)
    second = message("second")
    await asyncio.sleep(0.05)  # the second listener is open while the first turn runs
    orchestrator.hold["first"].set()
    await asyncio.wait_for(asyncio.gather(first, second), 10)

    answers = [t for t in _posted(say) if "Answer to the" in t]
    assert answers == ["Answer to the first.", "Answer to the second."]
