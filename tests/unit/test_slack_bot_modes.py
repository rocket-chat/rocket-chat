"""A bot's behaviour in Slack: the private response mode, routing, listening and direct messages.

Each test drives ``process_incoming_request`` through the shared-pipeline harness with a
recorded Slack client, and asserts what Slack received and which session the turn ran in.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from api.store import InMemorySessionStore
from slack_assistant.assistant import SlackAssistant
from slack_assistant.bot import (
    DISMISS,
    ClassifyRequest,
    RouteDecision,
    SlackBot,
    SlackRoute,
    SlackRouter,
    command_for,
)
from slack_assistant.private_threads import (
    PRIVATE_ANSWER_KEY,
    PRIVATE_THREAD_KEY,
    UNAVAILABLE_NOTICE,
    PrivateThread,
)
from slack_assistant.routing import ROUTE_KEY
from slack_assistant.toolset import NOT_CONNECTED
from slack_harness import make_slack

from specifications.interfaces.agent import AgentEvent, AgentOrchestratorProtocol

ANSWER = "The deploy failed because the image tag was missing."


class _Answering(AgentOrchestratorProtocol):
    def process_user_turn(self, session_id: str, user_message: str, **_: Any) -> Any:
        async def events() -> AsyncIterator[AgentEvent]:
            yield AgentEvent("thought", session_id, {"delta": ANSWER})

        return events()

    async def submit_human_approval(self, session_id: str, action_id: str, approved: bool) -> None:
        return None

    async def submit_question_answer(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def cancel_turn(self, session_id: str) -> None:
        return None


def _router(**overrides: Any) -> SlackRouter:
    values: dict[str, Any] = {
        "classifier_agent_id": "agent-classifier",
        "routes": (
            SlackRoute("ops", "Deployments and incidents", "agent-ops"),
            SlackRoute("code", "Code questions", "agent-code", response_mode="private"),
        ),
        "allow_dismiss": True,
        "min_confidence": 0.6,
        "fallback": "ops",
    }
    values.update(overrides)
    return SlackRouter(**values)


def _bot(**overrides: Any) -> SlackBot:
    values: dict[str, Any] = {
        "id": "a1b2c3d4e5f6",
        "org_id": "org_bots",
        "name": "Ops",
        "bot_token": "xoxb-test",
        "app_token": "xapp-test",
        "agent_id": "agent-default",
    }
    values.update(overrides)
    return SlackBot(**values)


def _slack(bot: SlackBot, decisions: list[RouteDecision | None] | None = None) -> SlackAssistant:
    asked: list[ClassifyRequest] = []
    queue = list(decisions or [])

    async def classify(request: ClassifyRequest) -> RouteDecision | None:
        asked.append(request)
        return queue.pop(0) if queue else None

    assistant = make_slack(
        _Answering(), session_store=InMemorySessionStore(), bot=bot, classify_fn=classify
    )
    assistant.classified = asked  # type: ignore[attr-defined]
    assistant._slack_api_ready = True
    assistant.bot_user_id = "UBOT"
    client = MagicMock()
    client.users_info = AsyncMock(return_value={"user": {"real_name": "Jane"}})
    client.conversations_replies = AsyncMock(return_value={"messages": []})
    for method in (
        "chat_postEphemeral",
        "chat_postMessage",
        "reactions_add",
        "chat_update",
        "agents_sessions_setStatus",
    ):
        setattr(client, method, AsyncMock(return_value={"ts": "9.9"}))
    assistant.app = MagicMock()
    assistant.app.client = client
    return assistant


async def _ask(assistant: SlackAssistant, text: str = "<@UBOT> why did deploy fail?") -> Any:
    say, status = AsyncMock(), AsyncMock()
    await assistant.process_incoming_request(
        channel_id="C1",
        thread_ts="100.1",
        raw_text=text,
        say_fn=say,
        status_fn=status,
        user_id="U1",
        channel_type="channel",
        team_id="T1",
        message_ts="100.1",
    )
    return say, status


def _dm_thread(assistant: SlackAssistant) -> AsyncMock:
    """The bot's DMs with U1: the first message opens thread 200.1 of channel D1."""
    post = assistant.app.client.chat_postMessage
    post.return_value = {"channel": "D1", "ts": "200.1"}
    assistant.app.client.chat_getPermalink = AsyncMock(
        return_value={"permalink": "https://slack/p100"}
    )
    return post  # type: ignore[no-any-return]


def _in_dm_thread(post: AsyncMock) -> list[dict[str, Any]]:
    return [c.kwargs for c in post.await_args_list if c.kwargs.get("thread_ts") == "200.1"]


async def test_a_private_request_leaves_only_a_reaction_and_runs_in_a_dm_thread() -> None:
    assistant = _slack(_bot(response_mode="private", private_reaction="eyes"))
    post = _dm_thread(assistant)

    say, _ = await _ask(assistant)

    client = assistant.app.client
    client.reactions_add.assert_awaited_once_with(channel="C1", timestamp="100.1", name="eyes")
    say.assert_not_awaited()
    client.chat_postEphemeral.assert_not_awaited()
    root = post.await_args_list[0].kwargs
    assert root["channel"] == "U1" and root.get("thread_ts") is None
    assert "https://slack/p100" in root["text"] and "<#C1>" in root["text"]
    assert "why did deploy fail?" in root["text"], "a preview of the request"
    replies = _in_dm_thread(post)
    assert replies and all(r["channel"] == "D1" for r in replies)
    answer = replies[-1]
    assert ANSWER in answer["text"]
    buttons = [e for b in answer["blocks"] if b["type"] == "actions" for e in b["elements"]]
    assert "share_private_answer" in [e.get("action_id") for e in buttons]
    dm_status = assistant.app.client.agents_sessions_setStatus.await_args.kwargs
    assert (dm_status["channel_id"], dm_status["thread_ts"]) == ("D1", "200.1"), (
        "the DM thread's session ends, so its stop button goes"
    )

    session_id = assistant.get_session_id("C1", "100.1", private_for="U1")
    record = await assistant.session_store.get_session(session_id)  # type: ignore[union-attr]
    assert record is not None and not record.is_shared
    assert record.tenant_user_id != "slack_global", "the requester's, not the workspace's"
    assert record.metadata[PRIVATE_ANSWER_KEY] == ANSWER
    assert record.metadata[PRIVATE_THREAD_KEY]["channel"] == "D1"
    thread = await assistant._private_threads.find("org_bots", "D1", "200.1")
    assert thread is not None and thread.session_id == session_id
    assert (
        await assistant.session_store.get_session(  # type: ignore[union-attr]
            assistant.get_session_id("C1", "100.1")
        )
        is None
    ), "no shared thread session"


async def test_a_later_mention_is_announced_in_the_same_dm_thread() -> None:
    assistant = _slack(_bot(response_mode="private"))
    post = _dm_thread(assistant)
    await _ask(assistant)

    await _ask(assistant, "<@UBOT> and the staging one?")

    roots = [c.kwargs for c in post.await_args_list if c.kwargs["channel"] == "U1"]
    assert len(roots) == 1, "one DM thread per private session"
    announced = [r for r in _in_dm_thread(post) if "New request" in str(r.get("text"))]
    assert len(announced) == 1 and "and the staging one?" in announced[0]["text"]
    calls = assistant.pipeline.calls  # type: ignore[attr-defined]
    assert calls[0]["session_id"] == calls[1]["session_id"]


async def test_a_reply_in_the_dm_thread_continues_the_private_session() -> None:
    assistant = _slack(_bot(response_mode="private", router=_router(sticky=False)))
    post = _dm_thread(assistant)
    await _ask(assistant)
    session_id = assistant.pipeline.calls[0]["session_id"]  # type: ignore[attr-defined]

    handled = await assistant._on_private_thread_message(
        {"channel": "D1", "thread_ts": "200.1", "ts": "201.1", "user": "U1", "text": "go on"}
    )

    assert handled
    second = assistant.pipeline.calls[1]  # type: ignore[attr-defined]
    assert second["session_id"] == session_id, "private, whatever the router now picks"
    assert "go on" in second["prompt"]
    assert assistant.app.client.reactions_add.await_count == 1, "no reaction in the DM"
    assert ANSWER in _in_dm_thread(post)[-1]["text"]


async def test_only_the_requester_continues_a_dm_thread() -> None:
    assistant = _slack(_bot(response_mode="private"))
    _dm_thread(assistant)
    await _ask(assistant)

    handled = await assistant._on_private_thread_message(
        {"channel": "D1", "thread_ts": "200.1", "ts": "201.1", "user": "U2", "text": "hi"}
    )

    assert handled and len(assistant.pipeline.calls) == 1  # type: ignore[attr-defined]


async def test_stop_in_the_dm_thread_stops_the_private_turn() -> None:
    assistant = _slack(_bot(response_mode="private"))
    _dm_thread(assistant)
    await _ask(assistant)
    session_id = assistant.pipeline.calls[0]["session_id"]  # type: ignore[attr-defined]
    assistant.orchestrator.cancel_turn = AsyncMock()  # type: ignore[method-assign]
    assistant._active_turns.add(session_id)

    await assistant._stop_agent_session(
        {"channel": "D1", "thread_ts": "200.1", "user": "U1"}, {"team_id": "T1"}
    )
    assistant.orchestrator.cancel_turn.assert_awaited_once_with(session_id)

    assistant._active_turns.add(session_id)
    await assistant._on_private_thread_message(
        {"channel": "D1", "thread_ts": "200.1", "ts": "202.1", "user": "U1", "text": "stop"}
    )
    assert assistant.orchestrator.cancel_turn.await_count == 2
    assert len(assistant.pipeline.calls) == 1, "a stop never becomes a request"  # type: ignore[attr-defined]


async def test_a_stranger_cannot_stop_a_private_turn_from_its_dm_thread() -> None:
    assistant = _slack(_bot(response_mode="private"))
    _dm_thread(assistant)
    await _ask(assistant)
    session_id = assistant.pipeline.calls[0]["session_id"]  # type: ignore[attr-defined]
    assistant.orchestrator.cancel_turn = AsyncMock()  # type: ignore[method-assign]
    assistant._active_turns.add(session_id)

    await assistant._stop_agent_session(
        {"channel": "D1", "thread_ts": "200.1", "user": "U2"}, {"team_id": "T1"}
    )

    assistant.orchestrator.cancel_turn.assert_not_awaited()


async def test_without_direct_messages_a_private_request_does_not_run() -> None:
    assistant = _slack(_bot(response_mode="private"))
    assistant.app.client.chat_postMessage.side_effect = RuntimeError("messages_tab_disabled")

    await _ask(assistant)

    assert assistant.pipeline.calls == []  # type: ignore[attr-defined]
    notice = assistant.app.client.chat_postEphemeral.await_args.kwargs
    assert notice["user"] == "U1" and notice["text"] == UNAVAILABLE_NOTICE
    assistant.app.client.reactions_add.assert_not_awaited()


async def test_only_the_requester_can_share_a_private_answer() -> None:
    assistant = _slack(_bot(response_mode="private"))
    _dm_thread(assistant)
    await _ask(assistant)
    session_id = assistant.get_session_id("C1", "100.1", private_for="U1")
    client = assistant.app.client
    client.chat_postMessage.reset_mock()

    await assistant._share_private_answer(
        {"actions": [{"value": session_id}], "user": {"id": "U2"}, "team": {"id": "T1"}}
    )
    client.chat_postMessage.assert_not_awaited()

    await assistant._share_private_answer(
        {"actions": [{"value": session_id}], "user": {"id": "U1"}, "team": {"id": "T1"}}
    )
    posted = client.chat_postMessage.await_args.kwargs
    assert posted["channel"] == "C1" and posted["thread_ts"] == "100.1"
    assert ANSWER in posted["text"]


async def test_a_dismissed_message_creates_no_session() -> None:
    assistant = _slack(_bot(router=_router()), [RouteDecision(DISMISS, 0.95, "small talk")])

    say, status = await _ask(assistant, "<@UBOT> haha nice")

    assert assistant.pipeline.calls == []  # type: ignore[attr-defined]
    say.assert_not_awaited()
    assert status.await_args_list[-1].kwargs["status"] == "", "the loader is cleared"
    assert assistant.session_store._sessions == {}  # type: ignore[union-attr]


async def test_the_chosen_route_picks_the_agent_and_sticks_to_the_thread() -> None:
    assistant = _slack(_bot(router=_router()), [RouteDecision("ops", 0.9, "a deploy")])

    await _ask(assistant)
    await _ask(assistant, "<@UBOT> and how do I fix it?")

    calls = assistant.pipeline.calls  # type: ignore[attr-defined]
    assert [c["overlay"].default_agent_id for c in calls] == ["agent-ops", "agent-ops"]
    assert len(assistant.classified) == 1, "the follow-up keeps the thread's route"  # type: ignore[attr-defined]
    record = await assistant.session_store.get_session(  # type: ignore[union-attr]
        assistant.get_session_id("C1", "100.1")
    )
    assert record is not None and record.metadata[ROUTE_KEY] == "ops"


async def test_an_unsure_decision_takes_the_fallback_and_a_route_can_be_private() -> None:
    unsure = _slack(_bot(router=_router()), [RouteDecision("code", 0.3, "maybe code")])
    await _ask(unsure)
    assert unsure.pipeline.calls[0]["overlay"].default_agent_id == "agent-ops"  # type: ignore[attr-defined]

    sure = _slack(_bot(router=_router()), [RouteDecision("code", 0.9, "code")])
    _dm_thread(sure)
    await _ask(sure)
    call = sure.pipeline.calls[0]  # type: ignore[attr-defined]
    assert call["overlay"].default_agent_id == "agent-code"
    assert call["session_id"].endswith("_U1"), "the route's private mode applies"


async def test_a_failed_classification_uses_the_fallback() -> None:
    assistant = _slack(_bot(router=_router(fallback="")), [None])

    await _ask(assistant)

    assert assistant.pipeline.calls[0]["overlay"].default_agent_id == "agent-default"  # type: ignore[attr-defined]


def test_listened_channels_and_direct_messages() -> None:
    listening = _slack(_bot(listen_channels=frozenset({"C1"}), respond_in_dms=False))

    assert listening._listens_to({"channel": "C1", "ts": "1.0", "text": "deploy is red"})
    assert not listening._listens_to({"channel": "C2", "ts": "1.0", "text": "deploy is red"})
    assert not listening._listens_to(
        {"channel": "C1", "ts": "2.0", "thread_ts": "1.0", "text": "a reply"}
    ), "thread replies are context, not new requests"
    assert not listening._listens_to({"channel": "C1", "ts": "1.0", "text": "<@UBOT> hi"}), (
        "a mention arrives as app_mention"
    )
    assert not listening._answers_direct_messages()
    assert _slack(_bot())._answers_direct_messages()


def test_a_click_on_a_private_gate_reaches_the_private_session() -> None:
    assistant = _slack(_bot())
    body = {
        "actions": [{"value": "act-1"}],
        "channel": {"id": "C1"},
        "container": {"thread_ts": "100.1", "is_ephemeral": True},
        "user": {"id": "U1"},
    }

    session_id, _, _, clicker = assistant._gate_context(body)

    assert clicker == "U1"
    assert session_id == assistant.get_session_id("C1", "100.1", private_for="U1")


@pytest.mark.parametrize("private_for", [None, "U0123456789"])
def test_bot_session_ids_fit_the_sessions_table(private_for: str | None) -> None:
    session_id = _slack(_bot()).get_session_id("C0123456789", "1700000000.123456", private_for)
    assert session_id.startswith("slack_a1b2c3d4_")
    assert len(session_id) <= 64 and "." not in session_id


async def test_a_channel_can_make_a_bot_private() -> None:
    channel = {"channel_profiles": {"C1": {"response_mode": "private"}}}
    assistant = _slack(_bot(policy=channel))
    _dm_thread(assistant)

    await _ask(assistant)

    assert assistant.pipeline.calls[0]["session_id"].endswith("_U1")  # type: ignore[attr-defined]


async def test_slack_tools_run_on_the_bot_that_owns_the_session() -> None:
    import asyncio

    from agent_core.context import ExecutionContext, current_execution_ctx

    tools: dict[str, Any] = {}
    registry = MagicMock()
    registry._dynamic_tools = tools
    registry.register_dynamic_tool = MagicMock(
        side_effect=lambda definition, handler, **_: tools.setdefault(definition.name, handler)
    )
    first = make_slack(_Answering(), tool_registry=registry, bot=_bot(id="aaaaaaaa1111"))
    second = make_slack(_Answering(), tool_registry=registry, bot=_bot(id="bbbbbbbb2222"))
    for assistant in (first, second):
        assistant._slack_api_ready = True
        assistant.app = MagicMock()
        assistant.app.client.chat_postMessage = AsyncMock(return_value={"ts": "1.1"})
    session_id = second.get_session_id("C9", "5.5")
    second._session_meta[session_id] = ("C9", "5.5")

    async def post(session: str) -> str:
        ctx = ExecutionContext(session_id=session, event_queue=asyncio.Queue(), parent_model="m")
        token = current_execution_ctx.set(ctx)
        try:
            return str(await tools["slack_send_message"](channel="C9", text="hi"))
        finally:
            current_execution_ctx.reset(token)

    assert "posted" in await post(session_id), "allowed: it is the session's own conversation"
    second.app.client.chat_postMessage.assert_awaited_once()
    first.app.client.chat_postMessage.assert_not_awaited()

    await second.stop_socket_mode()
    assert await post(session_id) == NOT_CONNECTED, "a stopped bot's sessions get no other bot"
    await first.stop_socket_mode()


async def test_a_web_session_uses_its_organizations_bot_for_slack_tools() -> None:
    import asyncio

    from agent_core.context import ExecutionContext, current_execution_ctx

    tools: dict[str, Any] = {}
    registry = MagicMock()
    registry._dynamic_tools = tools
    registry.register_dynamic_tool = MagicMock(
        side_effect=lambda definition, handler, **_: tools.setdefault(definition.name, handler)
    )
    ours = make_slack(
        _Answering(), tool_registry=registry, bot=_bot(id="cccccccc3333", org_id="org_web_slack")
    )
    theirs = make_slack(
        _Answering(), tool_registry=registry, bot=_bot(id="aaaaaaaa0000", org_id="org_other")
    )
    for assistant in (ours, theirs):
        assistant._slack_api_ready = True
        assistant.app = MagicMock()
        assistant.app.client.conversations_list = AsyncMock(
            return_value={"channels": [{"id": "C1", "name": assistant.bot.org_id}]}  # type: ignore[union-attr]
        )

    async def list_from(session: str, org: str) -> str:
        ctx = ExecutionContext(
            session_id=session, event_queue=asyncio.Queue(), parent_model="m", tenant_org_id=org
        )
        token = current_execution_ctx.set(ctx)
        try:
            return str(await tools["slack_list_conversations"]())
        finally:
            current_execution_ctx.reset(token)

    assert "#org_web_slack" in await list_from("session_web1", "org_web_slack")
    theirs.app.client.conversations_list.assert_not_awaited()
    assert await list_from("session_web2", "org_without_bot") == NOT_CONNECTED
    assert await list_from("slack_gone0000_C1_1", "org_web_slack") == NOT_CONNECTED, (
        "a session of a stopped bot is not taken over by another"
    )
    await ours.stop_socket_mode()
    await theirs.stop_socket_mode()


def test_each_bot_has_its_own_slash_command() -> None:
    assert command_for("Triage Bot") == "/triage-bot"
    assert command_for("dify") == "/dify"
    assert command_for("!!!") == "/rocket"
    assert _slack(_bot(command="/dify")).command == "/dify"
    assert make_slack(_Answering()).command == "/rocket", "an assistant without a bot"


@pytest.mark.parametrize("command", ["/dify", "/rocket", "/other"])
async def test_a_bot_answers_its_command_and_the_legacy_one(command: str) -> None:
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.authorization import AuthorizeResult
    from slack_bolt.request.async_request import AsyncBoltRequest

    async def authorize(**_: Any) -> AuthorizeResult:
        return AuthorizeResult(enterprise_id=None, team_id="T1", bot_token="xoxb-test")

    app = AsyncApp(authorize=authorize, request_verification_enabled=False)
    assistant = make_slack(_Answering(), bot=_bot(command="/dify"), app=app)
    assistant._rocket_command = AsyncMock()  # type: ignore[method-assign]
    body = {
        "command": command,
        "text": "status",
        "user_id": "U1",
        "channel_id": "C1",
        "team_id": "T1",
        "response_url": "https://hooks.slack.test/r",
        "trigger_id": f"trigger-{command}",
    }

    response = await app.async_dispatch(AsyncBoltRequest(body=body, mode="socket_mode"))

    handled = assistant._rocket_command.await_count == 1
    assert handled is (command != "/other"), response.status


async def test_a_gate_in_a_dm_thread_names_its_session() -> None:
    from slack_assistant.block_kit import build_question_blocks

    from specifications.interfaces.agent import InteractiveQuestion

    assistant = _slack(_bot())
    thread = PrivateThread("D1", "200.1", "session-private-1", "U1", "C1", "100.1")
    say = assistant._private_thread_say(thread)
    question = InteractiveQuestion("q-1", "Which branch?", ["main", "beta"], False, "beta")
    await say(blocks=build_question_blocks(question), text="Which branch?", thread_ts="100.1")

    sent = assistant.app.client.chat_postMessage.await_args.kwargs
    assert sent["channel"] == "D1" and sent["thread_ts"] == "200.1"
    buttons = next(b for b in sent["blocks"] if b["type"] == "actions")
    click = {
        "actions": [{"value": "beta", "block_id": buttons["block_id"]}],
        "channel": {"id": "D1"},
        "container": {"thread_ts": "200.1"},
        "user": {"id": "U1"},
    }

    session_id, _, _, clicker = assistant._gate_context(click)

    assert (session_id, clicker) == ("session-private-1", "U1")
