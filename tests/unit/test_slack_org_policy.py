"""The org's `slack` settings change what the assistant does."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from slack_assistant.assistant import SlackAssistant
from slack_assistant.config import SlackAssistantSettings
from slack_assistant.policy import SlackOrgPolicy
from slack_harness import make_slack

from specifications.interfaces.agent import AgentEvent, AgentOrchestratorProtocol


class _Orchestrator(AgentOrchestratorProtocol):
    def __init__(self) -> None:
        self.turns: list[dict[str, Any]] = []

    def process_user_turn(
        self, session_id: str, user_message: str, **kwargs: Any
    ) -> AsyncIterator[AgentEvent]:
        self.turns.append({"session_id": session_id, **kwargs})

        async def _gen() -> AsyncIterator[AgentEvent]:
            yield AgentEvent(
                event_type="thought",
                session_id=session_id,
                payload={"delta": "hi", "is_reasoning": False},
            )

        return _gen()

    async def submit_human_approval(self, *a: Any, **k: Any) -> None: ...
    async def submit_question_answer(self, *a: Any, **k: Any) -> None: ...
    async def cancel_turn(self, session_id: str) -> None: ...


def _assistant(policy: SlackOrgPolicy, **kwargs: Any) -> tuple[SlackAssistant, _Orchestrator]:
    orchestrator = _Orchestrator()

    async def resolve_policy(_org: str) -> SlackOrgPolicy:
        return policy

    assistant = make_slack(
        orchestrator=orchestrator,
        settings=SlackAssistantSettings(
            slack_user_turns_per_minute=0, slack_channel_turns_per_minute=0
        ),
        resolve_slack_policy_fn=resolve_policy,
        **kwargs,
    )
    assistant._resolve_slack_user_name = AsyncMock(return_value="Alice")  # type: ignore[method-assign]
    return assistant, orchestrator


async def _send(assistant: SlackAssistant, channel_type: str | None) -> AsyncMock:
    say = AsyncMock()
    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="1.1",
        raw_text="<@UBOT> do it",
        say_fn=say,
        user_id="U1",
        channel_type=channel_type,
        team_id="T1",
    )
    return say


@pytest.mark.asyncio
async def test_disallowed_channel_types_get_no_turn_and_no_reply() -> None:
    assistant, orchestrator = _assistant(SlackOrgPolicy(allowed_channel_types=frozenset({"im"})))
    say = await _send(assistant, "channel")
    assert orchestrator.turns == []
    say.assert_not_called()


@pytest.mark.asyncio
async def test_allowed_channel_types_run_a_turn() -> None:
    assistant, orchestrator = _assistant(
        SlackOrgPolicy(allowed_channel_types=frozenset({"public"}))
    )
    await _send(assistant, "channel")
    assert len(orchestrator.turns) == 1


@pytest.mark.asyncio
async def test_org_tool_allowlist_overrides_the_environment_default() -> None:
    registry = MagicMock()
    registry.get_tool_definitions.return_value = [
        MagicMock(**{"name": n}) for n in ("web_fetch", "bash_exec", "file_read")
    ]
    for definition, name in zip(
        registry.get_tool_definitions.return_value,
        ("web_fetch", "bash_exec", "file_read"),
        strict=True,
    ):
        definition.name = name
    registry.catalog.return_value = registry.get_tool_definitions.return_value
    assistant, orchestrator = _assistant(
        SlackOrgPolicy(allowed_tools=("web_fetch", "file_read")), tool_registry=registry
    )
    await _send(assistant, "channel")
    assert sorted(orchestrator.turns[0]["whitelisted_tools"]) == ["file_read", "web_fetch"]


@pytest.mark.asyncio
async def test_email_auto_link_can_be_disabled() -> None:
    resolve_user = AsyncMock(return_value=None)
    assistant, _ = _assistant(
        SlackOrgPolicy(auto_link_by_verified_email=False), resolve_user_fn=resolve_user
    )
    assistant._fetch_slack_user = AsyncMock(  # type: ignore[method-assign]
        return_value={"profile": {"email": "alice@acme.io"}}
    )
    await _send(assistant, "im")
    # Only the id-based lookup ran; the e-mail lookup was skipped.
    assert [call.args for call in resolve_user.await_args_list] == [("U1", None, "default_org")]

    resolve_user.reset_mock()
    linking, _ = _assistant(
        SlackOrgPolicy(auto_link_by_verified_email=True), resolve_user_fn=resolve_user
    )
    linking._fetch_slack_user = assistant._fetch_slack_user  # type: ignore[method-assign]
    await _send(linking, "im")
    assert [call.args for call in resolve_user.await_args_list] == [
        ("U1", None, "default_org"),
        ("U1", "alice@acme.io", "default_org"),
    ]


@pytest.mark.asyncio
async def test_member_can_turn_off_completion_notices() -> None:
    for enabled, expected_posts in ((True, 1), (False, 0)):
        notifications = AsyncMock(return_value=enabled)
        assistant, _ = _assistant(SlackOrgPolicy(), notifications_enabled_fn=notifications)
        assistant.settings.slack_proactive_notify_after_seconds = 0
        poster = AsyncMock()
        assistant.app = MagicMock()
        assistant.app.client.chat_postMessage = poster
        await _send(assistant, "im")
        assert poster.await_count == expected_posts, enabled
