"""Unit test suite for Slack Assistant and Block Kit formatters."""

import time
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from slack_assistant.assistant import SlackAssistant, clean_mention_text
from slack_assistant.block_kit import (
    build_approval_blocks,
    build_diff_blocks,
    build_question_blocks,
)
from slack_assistant.config import SlackAssistantSettings

from specifications.interfaces.agent import (
    AgentEvent,
    AgentOrchestratorProtocol,
    FileDiff,
    InteractiveQuestion,
)
from specifications.interfaces.sandbox import SandboxDriverProtocol


class DummyOrchestrator(AgentOrchestratorProtocol):
    """Stub orchestrator for unit testing Slack event handlers."""

    def __init__(self, events: list[AgentEvent] | None = None) -> None:
        self.events: list[AgentEvent] = events or []
        self.approved_actions: list[tuple[str, str, bool]] = []
        self.answered_questions: list[tuple[str, str, list[str], str | None]] = []

    def process_user_turn(
        self,
        session_id: str,
        user_message: str,
    ) -> AsyncIterator[AgentEvent]:
        async def _gen() -> AsyncIterator[AgentEvent]:
            for ev in self.events:
                yield ev

        return _gen()

    async def submit_human_approval(
        self,
        session_id: str,
        action_id: str,
        approved: bool,
    ) -> None:
        self.approved_actions.append((session_id, action_id, approved))

    async def submit_question_answer(
        self,
        session_id: str,
        question_id: str,
        selected_options: list[str],
        custom_text: str | None = None,
    ) -> None:
        self.answered_questions.append((session_id, question_id, selected_options, custom_text))

    async def cancel_turn(self, session_id: str) -> None:
        pass


def test_clean_mention_text() -> None:
    assert clean_mention_text("<@U12345678> fix the bug") == "fix the bug"
    assert clean_mention_text("<@U123> <@U456> run pytest") == "run pytest"
    assert clean_mention_text("no mention here") == "no mention here"


def test_get_session_id() -> None:
    session_id = SlackAssistant.get_session_id("C081234", "1700000000.123456")
    assert session_id == "slack_C081234_1700000000_123456"
    assert "." not in session_id


def test_build_diff_blocks() -> None:
    diff = FileDiff(
        path="math_utils.py",
        diff_content="--- math_utils.py\n+++ math_utils.py\n@@ -1 +1 @@\n-return a - b\n+return a + b",
        additions=1,
        deletions=1,
    )
    blocks = build_diff_blocks([diff])
    assert len(blocks) == 2
    assert "math_utils.py" in blocks[0]["text"]["text"]
    assert "```diff" in blocks[1]["text"]["text"]
    assert "+return a + b" in blocks[1]["text"]["text"]


def test_build_diff_blocks_truncation() -> None:
    huge_diff = "a" * 3500
    diff = FileDiff(
        path="huge.py",
        diff_content=huge_diff,
        additions=100,
        deletions=0,
    )
    blocks = build_diff_blocks([diff], max_chars=500)
    assert len(blocks) == 2
    assert "Diff truncated due to Slack size limits" in blocks[1]["text"]["text"]


def test_build_approval_blocks() -> None:
    blocks = build_approval_blocks(action_id="act_42", title="Deploy", description="Deploy changes")
    assert len(blocks) == 2
    assert blocks[1]["type"] == "actions"
    elements = blocks[1]["elements"]
    assert len(elements) == 2
    assert elements[0]["action_id"] == "approve_action"
    assert elements[0]["value"] == "act_42"
    assert elements[1]["action_id"] == "reject_action"
    assert elements[1]["value"] == "act_42"


def test_build_question_blocks() -> None:
    q = InteractiveQuestion(
        question_id="q1",
        question_text="Choose approach",
        options=["Fast", "Safe"],
        default_recommended_option="Safe",
    )
    blocks = build_question_blocks(q)
    assert len(blocks) >= 2
    assert "Choose approach" in blocks[0]["text"]["text"]
    elements = blocks[1]["elements"]
    assert len(elements) == 2
    assert "⭐ Safe" in elements[1]["text"]["text"]


def test_determine_spinner_message() -> None:
    orchestrator = DummyOrchestrator()
    assistant = SlackAssistant(orchestrator=orchestrator)

    assert "test suite" in assistant._determine_spinner_message(
        "bash_exec", {"command": "pytest tests/ -v"}
    )
    assert "git operation" in assistant._determine_spinner_message(
        "bash_exec", {"command": "git status"}
    )
    assert "echo hello" in assistant._determine_spinner_message(
        "bash_exec", {"command": "echo hello"}
    )
    assert "math.py" in assistant._determine_spinner_message("file_edit", {"path": "math.py"})
    assert "web" in assistant._determine_spinner_message("web_search", {})


@pytest.mark.asyncio
async def test_process_incoming_request_with_diff() -> None:
    diff = FileDiff(
        path="calc.py",
        diff_content="--- calc.py\n+++ calc.py\n@@ -1 +1 @@\n-def add: pass\n+def add(a, b): return a + b",
        additions=1,
        deletions=1,
    )
    events = [
        AgentEvent(
            event_type="tool_call",
            session_id="dummy",
            payload={"name": "bash_exec", "arguments": '{"command": "pytest"}'},
        ),
        AgentEvent(
            event_type="file_diff",
            session_id="dummy",
            payload={
                "path": diff.path,
                "diff_content": diff.diff_content,
                "additions": diff.additions,
                "deletions": diff.deletions,
            },
        ),
        AgentEvent(
            event_type="thought",
            session_id="dummy",
            payload={"delta": "I have resolved the issue."},
        ),
    ]

    orchestrator = DummyOrchestrator(events=events)
    assistant = SlackAssistant(orchestrator=orchestrator)

    say_mock = AsyncMock()
    status_mock = AsyncMock()

    await assistant.process_incoming_request(
        channel_id="C123",
        thread_ts="100.200",
        raw_text="<@U_BOT> fix calc.py",
        say_fn=say_mock,
        status_fn=status_mock,
    )

    # Verify status calls were made
    assert status_mock.call_count >= 2
    # Verify say called with Block Kit blocks
    say_mock.assert_called_once()
    _, kwargs = say_mock.call_args
    assert "blocks" in kwargs
    assert kwargs["thread_ts"] == "100.200"
    blocks = kwargs["blocks"]
    assert any("calc.py" in str(b) for b in blocks)
    assert any("approve_action" in str(b) for b in blocks)


@pytest.mark.asyncio
async def test_hibernate_inactive_sessions() -> None:
    driver_mock = MagicMock(spec=SandboxDriverProtocol)
    driver_mock.hibernate_sandbox = AsyncMock()

    orchestrator = DummyOrchestrator()
    settings = SlackAssistantSettings(auto_hibernate_seconds=10)
    assistant = SlackAssistant(
        orchestrator=orchestrator,
        sandbox_driver=driver_mock,
        settings=settings,
    )

    # Set up session activity timestamps relative to current time
    now = time.time()
    assistant._last_activity["session_active"] = now - 5.0
    assistant._last_activity["session_idle"] = now - 100.0

    # Hibernate with timeout of 50 seconds
    hibernated = await assistant.hibernate_inactive_sessions(timeout_seconds=50)
    assert "session_idle" in hibernated
    assert "session_active" not in hibernated
    driver_mock.hibernate_sandbox.assert_called_once_with("session_idle")
