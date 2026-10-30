"""Slack v2: follow-ups, channel repositories, pull request cards, publishing from shared
channels, interactive gates, concise progress, polish and shortcuts."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.tools.registry import ToolRegistry
from api.services.slack_bot_setup import manifest
from sandbox_driver.fake_driver import FakeSandboxDriver
from slack_assistant.assistant import SlackAssistant, _Admitted
from slack_assistant.block_kit import (
    QUESTION_CHECKBOXES_ACTION_ID,
    QUESTION_OTHER_ACTION_ID,
    approve_label,
    build_approval_blocks,
    build_question_blocks,
    session_block_id,
    session_of_block,
)
from slack_assistant.cards import (
    RETRY_ACTION_ID,
    PullRequestCard,
    TurnSummary,
    build_summary_card,
    ci_state_of,
    published_branch_of,
    pull_request_of,
    wait_notice,
)
from slack_assistant.policy import ChannelProfile, SlackOrgPolicy
from slack_assistant.refusals import failure_notice
from slack_assistant.shortcuts import parse_shortcuts
from slack_assistant.stream import TurnStream
from slack_harness import make_slack

from specifications.interfaces.agent import InteractiveQuestion, TurnPolicy
from specifications.interfaces.session import SessionRecord, SessionSource


class _Orchestrator:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.answers: list[tuple[str, str, list[str], str | None]] = []

    async def cancel_turn(self, session_id: str) -> None:
        self.cancelled.append(session_id)

    async def submit_question_answer(
        self,
        session_id: str,
        question_id: str,
        selected_options: list[str],
        custom_text: str | None = None,
    ) -> None:
        self.answers.append((session_id, question_id, selected_options, custom_text))


class _Store:
    """The few session-store calls the Slack assistant makes."""

    def __init__(self, *records: SessionRecord) -> None:
        self.records = {r.session_id: r for r in records}

    async def get_session(self, session_id: str, *_: Any, **__: Any) -> SessionRecord | None:
        return self.records.get(session_id)

    async def mutate_session(self, session_id: str, _org: str, fn: Any) -> bool:
        record = self.records.get(session_id)
        if record is None:
            return False
        fn(record)
        return True


def _assistant(**kwargs: Any) -> SlackAssistant:
    return make_slack(orchestrator=kwargs.pop("orchestrator", _Orchestrator()), **kwargs)  # type: ignore[arg-type]


def _admitted(**overrides: Any) -> _Admitted:
    base = _Admitted(
        prompt="p",
        title_text="p",
        team_scope="T1",
        is_dm=False,
        session_user_id="guest",
        session_org_id="org",
        rocket_user_id="member",
        org_policy=SlackOrgPolicy(),
        workspace_org="org",
        rocket_org_id="org",
        linked=True,
    )
    return replace(base, **overrides)


# -- P8.7 shortcuts ---------------------------------------------------------------------------


def test_shortcuts_are_stripped_and_become_choices() -> None:
    parsed = parse_shortcuts("!deep fix the flaky test !agent reviewer !repo acme/api@main")
    assert parsed.text == "fix the flaky test"
    assert (parsed.reasoning_effort, parsed.agent_name) == ("high", "reviewer")
    assert (parsed.repository, parsed.branch) == ("acme/api", "main")


def test_shortcuts_keep_the_lines_of_a_message() -> None:
    parsed = parse_shortcuts("!fast please run\n```\nmake test\n```")
    assert parsed.reasoning_effort == "low"
    assert parsed.text == "please run\n```\nmake test\n```"


def test_an_aside_is_not_a_request() -> None:
    assert parse_shortcuts("(aside) lunch?").aside
    assert not parse_shortcuts("what about the (aside) test?").aside


def test_a_message_without_shortcuts_is_unchanged() -> None:
    parsed = parse_shortcuts("deploy !important now")
    assert not parsed.any and parsed.text == "deploy !important now"


async def test_an_aside_is_dismissed_before_admission() -> None:
    assistant = _assistant()
    admitted = await assistant._admit(
        channel_id="C1",
        thread_ts="1.0",
        raw_text="(aside) not for the bot",
        say_fn=AsyncMock(),
        user_id="U1",
        channel_type="channel",
        team_id="T1",
        ephemeral_fn=None,
    )
    assert admitted is None


async def test_an_unknown_agent_shortcut_is_said_and_ignored() -> None:
    resolve = AsyncMock(return_value=None)
    assistant = _assistant(resolve_agent_fn=resolve)
    say = AsyncMock()
    view = MagicMock(say_fn=say, thread_ts="1.0")
    admitted = _admitted(shortcuts=parse_shortcuts("!agent ghost go"))
    assert await assistant._shortcut_agent(view, admitted) is None
    resolve.assert_awaited_once_with("org", "member", "ghost")
    assert "ghost" in say.await_args.kwargs["text"]


# -- P8.1 follow-ups in threads ---------------------------------------------------------------


def test_the_manifest_always_receives_channel_messages() -> None:
    record = MagicMock(
        listen_channels=[], handle="bot", name="Bot", respond_in_dms=True, command="/bot"
    )
    events = manifest(record)["settings"]["event_subscriptions"]["bot_events"]
    assert {"message.channels", "message.groups"} <= set(events)


async def test_a_reply_in_a_thread_with_a_session_is_a_follow_up() -> None:
    assistant = _assistant()
    session_id = assistant.get_session_id("C1", "10.0")
    assistant.session_store = _Store(_record(session_id))  # type: ignore[assignment]
    event = {
        "channel": "C1",
        "thread_ts": "10.0",
        "ts": "11.0",
        "user": "U1",
        "text": "can you also fix b?",
    }
    assert await assistant._is_thread_follow_up(event)
    assert not await assistant._is_thread_follow_up({**event, "thread_ts": "99.0"})
    assert not await assistant._is_thread_follow_up({**event, "text": "thanks"})


async def test_a_private_session_takes_follow_ups_from_its_requester_only() -> None:
    assistant = _assistant()
    private = assistant.get_session_id("C1", "10.0", private_for="U1")
    assistant.session_store = _Store(_record(private))  # type: ignore[assignment]
    event = {"channel": "C1", "thread_ts": "10.0", "ts": "11.0", "text": "please check"}
    assert await assistant._is_thread_follow_up({**event, "user": "U1"})
    assert not await assistant._is_thread_follow_up({**event, "user": "U2"})


# -- P8.2 channel repository --------------------------------------------------------------------


async def test_a_channel_repository_is_named_in_the_stable_prompt() -> None:
    assistant = _assistant()
    policy = SlackOrgPolicy(
        channel_profiles={"C1": ChannelProfile(repository="acme/api", branch="develop")}
    )
    overlay = await assistant._build_overlay(
        channel_id="C1",
        thread_ts="1.0",
        admitted=_admitted(org_policy=policy, linked=False),
        attachment_note="",
        attachments=(),
    )
    assert "`acme/api` (branch `develop`)" in overlay.stable_suffix
    assert "clone_repository" in overlay.stable_suffix


async def test_a_repo_shortcut_is_named_for_this_turn_only() -> None:
    assistant = _assistant()
    overlay = await assistant._build_overlay(
        channel_id="C1",
        thread_ts="1.0",
        admitted=_admitted(linked=False, shortcuts=parse_shortcuts("!repo acme/web go")),
        attachment_note="",
        attachments=(),
    )
    assert "acme/web" in overlay.dynamic
    assert "acme/web" not in overlay.stable_suffix


# -- P8.3 publishing from shared channels ------------------------------------------------------


async def test_a_linked_member_publishes_in_a_shared_thread_after_approving() -> None:
    assistant = _assistant()
    overlay = await assistant._build_overlay(
        channel_id="C1", thread_ts="1.0", admitted=_admitted(), attachment_note="", attachments=()
    )
    assert {"git_push", "git_open_pr"} <= overlay.gated_tools
    assert not overlay.denied_tools & {"git_push", "git_open_pr"}
    assert overlay.publish_as_user_id == "member"


@pytest.mark.parametrize(
    "overrides",
    [
        {"linked": False},
        {"rocket_org_id": "another_org"},
        {"org_policy": SlackOrgPolicy(restrict_linked_members=True)},
    ],
)
async def test_publishing_stays_blocked_for_guests_outsiders_and_restricted_members(
    overrides: dict[str, Any],
) -> None:
    assistant = _assistant()
    overlay = await assistant._build_overlay(
        channel_id="C1",
        thread_ts="1.0",
        admitted=_admitted(**overrides),
        attachment_note="",
        attachments=(),
    )
    assert {"git_push", "git_open_pr"} <= overlay.denied_tools
    assert not overlay.gated_tools and overlay.publish_as_user_id is None


async def test_a_gated_tool_asks_even_in_bypass_mode() -> None:
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id="s1")
    policy = replace(TurnPolicy(), permission_mode="bypass", gated_tools=frozenset({"git_push"}))
    token = current_execution_ctx.set(
        ExecutionContext(
            session_id="s1", event_queue=asyncio.Queue(), parent_model="m", policy=policy
        )
    )
    try:
        result = await registry.call_tool("git_push", {})
    finally:
        current_execution_ctx.reset(token)
    assert result.startswith("Blocked") and "approval" in result


# -- P8.3 pull request card ---------------------------------------------------------------------


def test_a_pull_request_and_branch_are_read_from_tool_results() -> None:
    opened = "Pull request opened: https://github.com/acme/api/pull/42"
    assert pull_request_of(opened) == ("acme/api", 42, "https://github.com/acme/api/pull/42")
    assert pull_request_of("Error: failed to open pull request") is None
    assert published_branch_of("Pushed acme/api@feat/x. Head: abc.") == "feat/x"


@pytest.mark.parametrize(
    "kind, outcome, state",
    [
        ("check_suite.completed", "success", "success"),
        ("workflow_run.completed", "failure", "failure"),
        ("pull_request.closed", "merged", "merged"),
        ("review.submitted", "approved", None),
    ],
)
def test_ci_state_follows_github_events(kind: str, outcome: str, state: str | None) -> None:
    assert ci_state_of(kind, outcome) == state


async def test_a_card_is_updated_in_place_when_ci_reports() -> None:
    assistant = _assistant()
    assistant._slack_api_ready = True
    session_id = "slack_C1_1_0"
    card = PullRequestCard("acme/api", 42, "https://x/pull/42", channel_id="C1", ts="5.0")
    record = _record(session_id, slack_pr_cards=[card.as_metadata()])
    assistant.session_store = _Store(record)  # type: ignore[assignment]
    assistant.app.client.chat_update = AsyncMock()  # type: ignore[method-assign]
    event = {
        "kind": "check_suite.completed",
        "outcome": "success",
        "repo": "acme/api",
        "pr_number": 42,
    }
    assert await assistant.update_pull_request_cards(session_id, "org", event) == 1
    assistant.app.client.chat_update.assert_awaited_once()
    assert "CI passed" in str(assistant.app.client.chat_update.await_args.kwargs["blocks"])
    assert record.metadata["slack_pr_cards"][0]["ci"] == "success"
    # The same state again changes nothing.
    assert await assistant.update_pull_request_cards(session_id, "org", event) == 0


# -- P8.4 interactive gates -----------------------------------------------------------------------


def test_approval_buttons_say_what_they_approve() -> None:
    assert approve_label("git_push") == "Approve git push"
    blocks = build_approval_blocks("a1", action_type="git_open_pr", session_id="s_1")
    actions = blocks[-1]
    assert actions["elements"][0]["text"]["text"] == "Approve git open pr"
    assert session_of_block(actions["block_id"]) == "s_1"


def test_a_multi_select_question_offers_checkboxes_and_other() -> None:
    question = InteractiveQuestion(
        question_id="q1", question_text="Which?", options=["a", "b", "c"], is_multi_select=True
    )
    elements = [
        e
        for b in build_question_blocks(question, session_id="s")
        if b["type"] == "actions"
        for e in b["elements"]
    ]
    action_ids = {e["action_id"] for e in elements}
    assert {
        QUESTION_CHECKBOXES_ACTION_ID,
        "question_submit",
        QUESTION_OTHER_ACTION_ID,
    } <= action_ids


def test_each_actions_block_of_a_question_has_its_own_block_id() -> None:
    question = InteractiveQuestion(
        question_id="q1", question_text="?", options=[str(i) for i in range(7)]
    )
    ids = [
        b["block_id"]
        for b in build_question_blocks(question, session_id="s_9")
        if b["type"] == "actions"
    ]
    assert len(ids) == len(set(ids)) == 2
    assert {session_of_block(i) for i in ids} == {"s_9"}
    assert session_block_id("s_9") == "rocket-session:s_9"


async def test_a_clicked_approval_becomes_its_decision() -> None:
    orchestrator = _Orchestrator()
    orchestrator.submit_human_approval = AsyncMock()  # type: ignore[attr-defined]
    assistant = _assistant(orchestrator=orchestrator)
    assistant._gate_owners["a1"] = ("s1", "U1")
    respond = AsyncMock()
    body = {
        "actions": [{"value": "a1", "block_id": session_block_id("s1")}],
        "user": {"id": "U1"},
        "channel": {"id": "C1"},
        "container": {"thread_ts": "1.0"},
        "message": {
            "blocks": [{"type": "section", "text": {"text": "⚠️ *Approval Required — git push*"}}]
        },
    }
    await assistant._resolve_approval(body, respond, approved=True)
    kwargs = respond.await_args.kwargs
    assert kwargs["replace_original"] is True
    assert "Approved" in kwargs["text"] and "actions" not in str(kwargs["blocks"])


async def test_someone_else_cannot_answer_and_the_gate_stays() -> None:
    assistant = _assistant()
    assistant._gate_owners["a1"] = ("s1", "U1")
    respond = AsyncMock()
    body = {
        "actions": [{"value": "a1"}],
        "user": {"id": "U2"},
        "channel": {"id": "C1"},
        "container": {"thread_ts": "1.0"},
    }
    await assistant._resolve_approval(body, respond, approved=True)
    assert respond.await_args.kwargs["replace_original"] is False


async def test_checked_options_are_submitted_together() -> None:
    orchestrator = _Orchestrator()
    assistant = _assistant(orchestrator=orchestrator)
    assistant._gate_owners["q1"] = ("s1", "U1")
    body = {
        "actions": [{"value": "q1", "block_id": session_block_id("s1")}],
        "user": {"id": "U1"},
        "channel": {"id": "C1"},
        "container": {"thread_ts": "1.0"},
        "state": {
            "values": {
                "b": {
                    QUESTION_CHECKBOXES_ACTION_ID: {
                        "selected_options": [{"value": "a"}, {"value": "c"}]
                    }
                }
            }
        },
    }
    await assistant._submit_checked_options(body, AsyncMock(), AsyncMock())
    assert orchestrator.answers == [("s1", "q1", ["a", "c"], None)]


async def test_an_other_answer_is_sent_as_free_text() -> None:
    orchestrator = _Orchestrator()
    assistant = _assistant(orchestrator=orchestrator)
    assistant._gate_owners["q1"] = ("s1", "U1")
    assistant.app.client.chat_update = AsyncMock()  # type: ignore[method-assign]
    view = {
        "private_metadata": '{"session_id": "s1", "question_id": "q1", "channel_id": "C1", "message_ts": "2.0"}',
        "state": {"values": {"answer": {"text": {"value": "use the staging cluster"}}}},
    }
    await assistant._submit_other_answer({"user": {"id": "U1"}}, view)
    assert orchestrator.answers == [("s1", "q1", [], "use the staging cluster")]
    assistant.app.client.chat_update.assert_awaited_once()


# -- P8.5 concise progress ------------------------------------------------------------------------


async def test_the_checklist_is_shown_as_task_items_once_per_change() -> None:
    client = MagicMock()
    client.chat_startStream = AsyncMock(return_value={"ts": "9.0"})
    client.chat_appendStream = AsyncMock()
    stream = TurnStream(
        client, "C1", "1.0", recipient_user_id=None, recipient_team_id=None, update_interval=0
    )
    await stream.tool_started("file_read", {"path": "a"}, "Reading a")
    tasks = [
        {"title": "Write tests", "status": "in_progress"},
        {"title": "Fix", "status": "pending"},
    ]
    await stream.plan(tasks)
    await stream.plan(tasks)
    chunks = [c.kwargs["chunks"][0] for c in client.chat_appendStream.await_args_list]
    assert [(c["id"], c["status"]) for c in chunks] == [
        ("plan-0", "in_progress"),
        ("plan-1", "pending"),
    ]


def test_the_summary_card_says_how_the_turn_ended() -> None:
    blocks = build_summary_card(
        TurnSummary(
            status="waiting",
            elapsed_seconds=125,
            changes="Edited 2 files",
            cost_usd=0.42,
            wait_notice=wait_notice("ci_status", "acme/api#7"),
        ),
        link_url="https://rocket/chat/s",
        retry_value=None,
    )
    text = str(blocks)
    assert "Waiting for CI on acme/api#7" in text
    assert "2m 05s" in text and "$0.42" in text and "Open in Rocket Chat" in text
    assert RETRY_ACTION_ID not in text


def test_a_failed_turn_offers_retry() -> None:
    blocks = build_summary_card(TurnSummary(status="failed"), link_url=None, retry_value="s1")
    assert RETRY_ACTION_ID in str(blocks)


# -- P8.6 polish ----------------------------------------------------------------------------------


def test_a_shared_thread_never_sees_the_raw_error() -> None:
    raw = "ConnectError: https://10.0.0.4:8443/internal refused"
    assert "10.0.0.4" not in failure_notice(raw, shared=True)
    assert "10.0.0.4" in failure_notice(raw, shared=False)


@pytest.mark.setting("slack.dm_continue_minutes")
def test_a_direct_message_continues_the_last_conversation_within_the_window() -> None:
    assistant = _assistant()
    now = [1000.0]
    assistant._clock = lambda: now[0]
    window = SlackOrgPolicy.from_settings({"dm_continue_minutes": 30}).dm_continue_minutes
    assert assistant._continued_dm_thread("D1", "U1", "1.0", window) is None
    now[0] += 60
    assert assistant._continued_dm_thread("D1", "U1", "2.0", window) == "1.0"
    now[0] += 31 * 60
    assert assistant._continued_dm_thread("D1", "U1", "3.0", window) is None
    off = SlackOrgPolicy.from_settings({"dm_continue_minutes": 0}).dm_continue_minutes
    assert assistant._continued_dm_thread("D1", "U1", "4.0", off) is None


async def test_deleting_the_request_of_a_running_turn_stops_it() -> None:
    orchestrator = _Orchestrator()
    assistant = _assistant(orchestrator=orchestrator)
    session_id = assistant.get_session_id("C1", "1.0")
    record = _record(
        session_id, slack_turn_requester="U1", slack_last_request={"text": "do it", "ts": "1.0"}
    )
    assistant.session_store = _Store(record)  # type: ignore[assignment]
    assistant._active_turns.add(session_id)
    await assistant._on_message_deleted(
        {"channel": "C1", "deleted_ts": "1.0", "previous_message": {"ts": "1.0", "user": "U1"}}
    )
    assert orchestrator.cancelled == [session_id]
    assert "deleted" in assistant._stop_notices[session_id]


async def test_retry_is_only_for_the_requester() -> None:
    assistant = _assistant()
    session_id = "slack_C1_1_0"
    record = _record(
        session_id,
        slack_turn_requester="U1",
        slack_last_request={"text": "run tests", "ts": "1.0"},
        slack_channel_id="C1",
        slack_thread_ts="1.0",
    )
    assistant.session_store = _Store(record)  # type: ignore[assignment]
    assistant._run_in_thread = AsyncMock()  # type: ignore[method-assign]
    await assistant._retry_request({"actions": [{"value": session_id}], "user": {"id": "U2"}})
    assistant._run_in_thread.assert_not_awaited()
    await assistant._retry_request({"actions": [{"value": session_id}], "user": {"id": "U1"}})
    assert assistant._run_in_thread.await_args.kwargs["prompt"] == "run tests"


def test_app_home_names_the_bot_and_the_shortcuts() -> None:
    from slack_assistant.block_kit import build_home_view

    view = build_home_view(linked_as="Ada Lovelace", sessions=[], web_url=None, bot_name="Triage")
    text = str(view)
    assert "Ada Lovelace" in text and "@Triage" in text and "!repo" in text


def _record(session_id: str, **metadata: Any) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        tenant_org_id="org",
        tenant_user_id="guest",
        title="t",
        source=SessionSource.SLACK,
        metadata=dict(metadata),
    )
