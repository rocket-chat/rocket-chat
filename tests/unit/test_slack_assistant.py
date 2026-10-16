"""Unit test suite for Slack Assistant and Block Kit formatters."""

import json
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from slack_assistant.assistant import clean_mention_text
from slack_assistant.block_kit import (
    build_approval_blocks,
    build_diff_blocks,
    build_question_blocks,
    markdown_to_slack_mrkdwn,
)
from slack_assistant.config import SlackAssistantSettings
from slack_assistant.state import SlackAssistantState
from slack_harness import make_slack

from specifications.interfaces.agent import (
    AgentEvent,
    AgentOrchestratorProtocol,
    FileDiff,
    InteractiveQuestion,
    SystemPrompt,
)
from specifications.interfaces.sandbox import SandboxDriverProtocol


class DummyOrchestrator(AgentOrchestratorProtocol):
    """Stub orchestrator for unit testing Slack event handlers."""

    def __init__(self, events: list[AgentEvent] | None = None) -> None:
        self.events: list[AgentEvent] = events or []
        self.approved_actions: list[tuple[str, str, bool]] = []
        self.answered_questions: list[tuple[str, str, list[str], str | None]] = []
        self.last_callbacks: object | None = None

    def process_user_turn(
        self,
        session_id: str,
        user_message: str,
        model: str | None = None,
        system_prompt: str | None = None,
        whitelisted_tools: list[str] | None = None,
        tenant_org_id: str = "default_org",
        tenant_user_id: str = "dev_user",
        max_turns: int | None = None,
        **kwargs: object,
    ) -> AsyncIterator[AgentEvent]:
        self.last_callbacks = kwargs.get("callbacks")

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
    session_id = SlackAssistantState().get_session_id("C081234", "1700000000.123456")
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
    assistant = make_slack(orchestrator=orchestrator)

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
    assistant = make_slack(orchestrator=orchestrator)

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
    # No fake "commit" button: nothing in Slack backs it, so it must not be offered.
    assert not any("approve_action" in str(b) for b in blocks)


@pytest.mark.asyncio
async def test_hibernate_inactive_sessions() -> None:
    driver_mock = MagicMock(spec=SandboxDriverProtocol)
    driver_mock.hibernate_sandbox = AsyncMock()

    orchestrator = DummyOrchestrator()
    settings = SlackAssistantSettings(auto_hibernate_seconds=10)
    assistant = make_slack(
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


def test_markdown_to_slack_mrkdwn_code_blocks_and_placeholders() -> None:
    raw_md = """Here is **bold text** and `inline_var` with __double underscore bold__.

```python
def example():
    return "__inside_code_block__"
```

Another line with `another_var` and ~~strikethrough~~.
"""
    mrkdwn = markdown_to_slack_mrkdwn(raw_md)

    # Must NOT leak internal replacement placeholders
    assert "CODE_BLOCK" not in mrkdwn
    assert "INLINE_CODE" not in mrkdwn
    assert "@@" not in mrkdwn

    # Bold and strike conversions
    assert "*bold text*" in mrkdwn
    assert "*double underscore bold*" in mrkdwn
    assert "~strikethrough~" in mrkdwn

    # Inline codes preserved intact
    assert "`inline_var`" in mrkdwn
    assert "`another_var`" in mrkdwn

    # Code block preserved intact without losing internal content
    assert '```\ndef example():\n    return "__inside_code_block__"\n```' in mrkdwn


def test_markdown_to_slack_mrkdwn_tables() -> None:
    raw_table = """Here is a summary table:

| Status | Code | Description |
| :--- | :---: | ---: |
| Success | 200 | Request succeeded |
| Error | 500 | Internal server failure |

Continue execution.
"""
    mrkdwn = markdown_to_slack_mrkdwn(raw_table)

    # Tables converted to monospace ASCII blocks
    assert "```\n" in mrkdwn
    assert "Status  | Code | Description" in mrkdwn
    assert "--------+------+------------------------" in mrkdwn
    assert "Success | 200  | Request succeeded" in mrkdwn
    assert "Error   | 500  | Internal server failure" in mrkdwn
    assert "Continue execution." in mrkdwn


@pytest.mark.asyncio
async def test_slack_assistant_status_loader_updates_and_no_raw_messages() -> None:
    """Verify status events and update_status update Slack loader inplace and never post raw messages."""
    orchestrator = MagicMock()

    async def mock_events(*args: Any, **kwargs: Any) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            event_type="status",
            session_id="slack_C_STATUS_999",
            payload={"status": "running", "message": "Investigating repository layout..."},
        )
        yield AgentEvent(
            event_type="tool_call",
            session_id="slack_C_STATUS_999",
            payload={
                "name": "update_status",
                "arguments": json.dumps({"message": "Running auth test suite"}),
            },
        )
        yield AgentEvent(
            event_type="thought",
            session_id="slack_C_STATUS_999",
            payload={"delta": "Here is the completed test analysis.", "is_reasoning": False},
        )

    orchestrator.process_user_turn = MagicMock(side_effect=mock_events)

    assistant = make_slack(orchestrator=orchestrator)
    say_mock = AsyncMock()
    status_mock = AsyncMock()

    await assistant.process_incoming_request(
        channel_id="C_STATUS",
        thread_ts="999.000",
        raw_text="Run diagnostics",
        say_fn=say_mock,
        status_fn=status_mock,
    )

    # status_fn should receive:
    # 1. "Analyzing request..."
    # 2. "Investigating repository layout..." (from AgentEvent status)
    # 3. "Running auth test suite" (from update_status tool call)
    # 4. "" (cleanup in finally)
    status_calls = [c.kwargs.get("status") for c in status_mock.call_args_list]
    assert "Analyzing request..." in status_calls
    assert "Investigating repository layout..." in status_calls
    assert "Running auth test suite" in status_calls
    assert "" in status_calls

    loading_calls = [
        c.kwargs.get("loading_messages")
        for c in status_mock.call_args_list
        if c.kwargs.get("loading_messages")
    ]
    assert ["Analyzing request..."] in loading_calls
    assert ["Running auth test suite"] in loading_calls

    # say_mock should ONLY receive the final response, NEVER intermediate statuses
    assert say_mock.call_count == 1
    call_text = say_mock.call_args.kwargs.get("text")
    assert call_text == "Here is the completed test analysis."
    assert "Spinning up sandbox" not in call_text
    assert "Investigating repository" not in call_text


def test_build_question_blocks_action_ids_are_unique() -> None:
    """Slack rejects duplicate action_ids within one message; each option must be unique."""
    q = InteractiveQuestion(
        question_id="q1",
        question_text="Pick a deployment target",
        options=["Staging", "Production", "Canary", "Edge", "Local", "On-prem"],
    )
    blocks = build_question_blocks(q)
    action_ids = [
        element["action_id"]
        for block in blocks
        if block.get("type") == "actions"
        for element in block["elements"]
    ]
    assert len(action_ids) == 6
    assert len(set(action_ids)) == 6
    # Values still encode the question/option pair for the regex-matched handler.
    assert blocks[1]["elements"][0]["value"] == "q1:Staging"


def test_markdown_table_with_inline_code_does_not_leak_placeholders() -> None:
    """Regression: inline-code sentinels must never survive inside converted tables."""
    table = (
        "| Outil | Rôle |\n"
        "| --- | --- |\n"
        "| **`bash_exec`** | Exécuter des commandes shell |\n"
        "| **`file_read`** | Lire un fichier |\n"
    )
    output = markdown_to_slack_mrkdwn(table)
    assert "@@ROCKET" not in output
    assert "bash_exec" in output
    assert "file_read" in output
    # Emphasis markers are stripped because Slack does not render them in code blocks.
    assert "**" not in output


class CapturingOrchestrator(AgentOrchestratorProtocol):
    """Stub orchestrator capturing the prompt and system prompt for assertions."""

    def __init__(self) -> None:
        self.last_user_message: str | None = None
        self.last_system_prompt: str | None = None

    def process_user_turn(
        self,
        session_id: str,
        user_message: str,
        model: str | None = None,
        system_prompt: str | None = None,
        system_prompt_parts: SystemPrompt | None = None,
        whitelisted_tools: list[str] | None = None,
        tenant_org_id: str = "default_org",
        tenant_user_id: str = "dev_user",
        max_turns: int | None = None,
        **kwargs: object,
    ) -> AsyncIterator[AgentEvent]:
        self.last_user_message = user_message
        self.last_system_prompt = (
            system_prompt_parts.render() if system_prompt_parts is not None else system_prompt
        )

        async def _gen() -> AsyncIterator[AgentEvent]:
            if False:  # pragma: no cover - yields an empty event stream
                yield AgentEvent(event_type="status", session_id=session_id, payload={})

        return _gen()

    async def submit_human_approval(self, session_id: str, action_id: str, approved: bool) -> None:
        pass

    async def submit_question_answer(
        self,
        session_id: str,
        question_id: str,
        selected_options: list[str],
        custom_text: str | None = None,
    ) -> None:
        pass

    async def cancel_turn(self, session_id: str) -> None:
        pass


def _mock_slack_client() -> Any:
    client = MagicMock()

    async def users_info(user: str) -> dict[str, Any]:
        names = {"U1": "Jane Doe", "U2": "Bob Smith"}
        return {"user": {"id": user, "profile": {"display_name": names.get(user, user)}}}

    async def conversations_replies(**kwargs: Any) -> dict[str, Any]:
        return {
            "messages": [
                {"ts": "1.0", "user": "U1", "text": "<@UBOT> earlier question"},
                {"ts": "2.0", "user": "U2", "text": "a reply not mentioning the bot"},
            ]
        }

    client.users_info = AsyncMock(side_effect=users_info)
    client.conversations_replies = AsyncMock(side_effect=conversations_replies)
    return client


@pytest.mark.asyncio
async def test_speaker_identity_and_thread_context_injected() -> None:
    """The agent is told who is speaking and receives the whole thread transcript."""
    orch = CapturingOrchestrator()
    assistant = make_slack(orchestrator=orch)
    assistant._slack_api_ready = True
    assistant.bot_user_id = "UBOT"
    assistant.app = MagicMock()
    assistant.app.client = _mock_slack_client()

    say_mock = AsyncMock()
    status_mock = AsyncMock()
    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="1.0",
        raw_text="<@UBOT> please do the thing",
        say_fn=say_mock,
        status_fn=status_mock,
        user_id="U1",
    )

    assert orch.last_user_message is not None
    assert "[Speaker: Jane Doe (<@U1>)]" in orch.last_user_message

    assert orch.last_system_prompt is not None
    assert "[Speaker:" in orch.last_system_prompt
    assert "Slack Thread Context" in orch.last_system_prompt
    # A message that never mentioned the bot is still part of the context.
    assert "a reply not mentioning the bot" in orch.last_system_prompt
    assert "Bob Smith" in orch.last_system_prompt


@pytest.mark.asyncio
async def test_slack_context_tools_are_registered_and_readable() -> None:
    """On-demand thread/channel history tools are registered and resolve session context."""
    import asyncio

    from agent_core.context import ExecutionContext, current_execution_ctx

    orch = CapturingOrchestrator()
    captured: dict[str, Any] = {}

    def _register(definition: Any, handler: Any, **_kwargs: Any) -> None:
        captured[definition.name] = handler

    registry = MagicMock()
    registry._dynamic_tools = {}
    registry.register_dynamic_tool = MagicMock(side_effect=_register)

    assistant = make_slack(orchestrator=orch, tool_registry=registry)
    assert "slack_read_thread" in captured
    assert "slack_read_channel_history" in captured

    assistant._slack_api_ready = True
    assistant.bot_user_id = "UBOT"
    assistant.app = MagicMock()
    assistant.app.client = _mock_slack_client()
    assistant._session_meta["slack_C1_1_0"] = ("C1", "1.0")

    ctx = ExecutionContext(
        session_id="slack_C1_1_0", event_queue=asyncio.Queue(), parent_model="gpt-4o"
    )
    token = current_execution_ctx.set(ctx)
    try:
        thread_output = await captured["slack_read_thread"](limit=10)
    finally:
        current_execution_ctx.reset(token)

    assert "a reply not mentioning the bot" in thread_output
    assert "Jane Doe" in thread_output


@pytest.mark.asyncio
async def test_approval_required_renders_block_kit_approval_buttons() -> None:
    """The Slack assistant must surface the approval gate as actionable buttons."""
    event = AgentEvent(
        event_type="approval_required",
        session_id="slack_C_APPROVE_1_0",
        payload={
            "action_id": "ap_slack_1",
            "action_type": "git_revert_hard",
            "command_or_path": "Discard uncommitted work",
            "risk_level": "critical",
        },
    )
    assistant = make_slack(orchestrator=DummyOrchestrator(events=[event]))
    say_mock = AsyncMock()
    status_mock = AsyncMock()

    await assistant.process_incoming_request(
        channel_id="C_APPROVE",
        thread_ts="1.0",
        raw_text="<@UBOT> reset the workspace",
        say_fn=say_mock,
        status_fn=status_mock,
    )

    blocks = [
        block for call in say_mock.call_args_list for block in (call.kwargs.get("blocks") or [])
    ]
    rendered = json.dumps(blocks)
    assert "approve_action" in rendered
    assert "reject_action" in rendered
    assert "ap_slack_1" in rendered
    assert "git_revert_hard" in rendered
