"""Slack payloads through the real Bolt app, with a recording Slack client.

Unit tests call the assistant's methods directly, which cannot catch a handler that was never
registered, an event name that is wrong or a payload field that moved. These dispatch payloads
shaped like Slack's own (``tests/fixtures/slack``) through ``AsyncApp`` exactly as Socket Mode
does, against the real turn pipeline with a scripted model. Only Slack's HTTP API is replaced.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from slack_bolt.async_app import AsyncApp
from slack_bolt.authorization import AuthorizeResult
from slack_bolt.request.async_request import AsyncBoltRequest
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse
from test_slack_shared_pipeline import ScriptedModel, Stack, _wait_for

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "slack"


def payload(name: str, **overrides: Any) -> dict[str, Any]:
    body = json.loads((FIXTURES / f"{name}.json").read_text())
    body.update(overrides)
    return body


class SlackApi:
    """Stands in for Slack's HTTP API: every call succeeds and is remembered.

    Bolt hands each listener a copy of the client, so the replacement is installed on the
    ``AsyncWebClient`` class rather than on one instance.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def api_call(
        self, client: AsyncWebClient, api_method: str, **kwargs: Any
    ) -> AsyncSlackResponse:
        sent = kwargs.get("json") or kwargs.get("data") or kwargs.get("params") or {}
        self.calls.append((api_method, dict(sent)))
        data = {"ok": True, "ts": f"9{len(self.calls)}.0", "channel": sent.get("channel", "C1")}
        return AsyncSlackResponse(
            client=client,
            http_verb="POST",
            api_url="https://slack.test/api/",
            req_args={},
            data=data,
            headers={},
            status_code=200,
        )

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]

    def sent(self, method: str) -> list[dict[str, Any]]:
        return [args for name, args in self.calls if name == method]


class BoltStack:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.client = SlackApi()
        monkeypatch.setattr(
            AsyncWebClient,
            "api_call",
            lambda client, api_method, **kwargs: self.client.api_call(client, api_method, **kwargs),
        )
        self.responses: list[dict[str, Any]] = []

        async def authorize(**_: Any) -> AuthorizeResult:
            return AuthorizeResult(
                enterprise_id=None,
                team_id="T1",
                bot_token="xoxb-test",
                bot_id="B1",
                bot_user_id="UBOT",
            )

        self.app = AsyncApp(
            client=AsyncWebClient(token="xoxb-test"),
            authorize=authorize,
            signing_secret="unused",
            request_verification_enabled=False,
            ignoring_self_events_enabled=False,
            process_before_response=True,
        )
        self.stack = Stack(ScriptedModel(final_text="Fixed it."), app=self.app)
        self.assistant = self.stack.assistant
        self.assistant.bot_user_id = "UBOT"
        self.assistant.team_id = "T1"
        self.assistant._slack_api_ready = True
        self.assistant.settings = self.assistant.settings.model_copy(
            update={"slack_web_base_url": "https://rocket.example"}
        )

        async def record_response(webhook: Any, body: dict[str, Any]) -> Any:
            self.responses.append(body)
            return SimpleNamespace(status_code=200, body="ok")

        monkeypatch.setattr(
            "slack_sdk.webhook.async_client.AsyncWebhookClient.send_dict",
            record_response,
            raising=True,
        )

    async def dispatch(self, body: dict[str, Any]) -> Any:
        response = await self.app.async_dispatch(AsyncBoltRequest(body=body, mode="socket_mode"))
        await asyncio.sleep(0)
        return response

    async def settle(self) -> None:
        """Wait for the background work a handler started (a turn runs after the ack)."""
        for task in list(self.assistant._background):
            await asyncio.wait_for(task, 10)


@pytest.fixture
def bolt(monkeypatch: pytest.MonkeyPatch) -> BoltStack:
    return BoltStack(monkeypatch)


@pytest.mark.asyncio
async def test_a_mention_runs_a_turn_and_answers_in_its_thread(bolt: BoltStack) -> None:
    await bolt.dispatch(payload("event_app_mention"))
    await bolt.settle()

    posted = bolt.client.sent("chat.postMessage")
    assert posted, "nothing was posted"
    assert all(args.get("thread_ts") == "1700000000.000100" for args in posted)
    assert "Fixed it." in json.dumps(posted)
    assert "stop_turn" in json.dumps(posted[0]), "the live message carries a Stop button"
    assert bolt.client.sent("chat.update"), "the live message is finished in place"


@pytest.mark.asyncio
async def test_a_redelivered_event_is_handled_once(bolt: BoltStack) -> None:
    body = payload("event_app_mention")
    await bolt.dispatch(body)
    await bolt.settle()
    first = len(bolt.client.calls)

    await bolt.dispatch(body)
    await bolt.settle()

    assert len(bolt.client.calls) == first
    assert bolt.assistant._health.redelivered_events == 1
    assert bolt.assistant.quota_counters()["redelivered_events_dropped"] == 1


@pytest.mark.asyncio
async def test_a_direct_message_is_answered(bolt: BoltStack) -> None:
    await bolt.dispatch(payload("event_message_im"))
    await bolt.settle()
    assert "Fixed it." in json.dumps(bolt.client.sent("chat.postMessage"))


@pytest.mark.asyncio
async def test_messages_from_bots_are_ignored(bolt: BoltStack) -> None:
    await bolt.dispatch(payload("event_message_from_bot"))
    await bolt.settle()
    assert bolt.client.calls == []


@pytest.mark.asyncio
async def test_the_help_command_replies_privately(bolt: BoltStack) -> None:
    await bolt.dispatch(payload("command_rocket"))
    assert bolt.responses and bolt.responses[0]["response_type"] == "ephemeral"
    assert "/rocket new" in bolt.responses[0]["text"]


@pytest.mark.asyncio
async def test_rocket_new_starts_a_thread_and_works_in_it(bolt: BoltStack) -> None:
    body = payload("command_rocket", text="new update the changelog", trigger_id="TRIGGER_NEW_1")
    await bolt.dispatch(body)
    await bolt.settle()

    opening = bolt.client.sent("chat.postMessage")[0]
    assert "asked Rocket: update the changelog" in opening["text"]
    replies = [a for a in bolt.client.sent("chat.postMessage") if a.get("thread_ts")]
    assert replies and "Fixed it." in json.dumps(replies)


@pytest.mark.asyncio
async def test_rocket_status_lists_threads_and_says_when_there_are_none(bolt: BoltStack) -> None:
    await bolt.dispatch(payload("command_rocket", text="status", trigger_id="T_STATUS_1"))
    assert "no Slack threads" in bolt.responses[-1]["text"]

    await bolt.dispatch(payload("event_app_mention"))
    await bolt.settle()
    await bolt.dispatch(payload("command_rocket", text="status", trigger_id="T_STATUS_2"))
    assert "slack_C1_1700000000_000100" in bolt.responses[-1]["text"]


@pytest.mark.asyncio
async def test_the_message_shortcut_works_on_that_message(bolt: BoltStack) -> None:
    await bolt.dispatch(payload("shortcut_ask_rocket"))
    await bolt.settle()

    replies = bolt.client.sent("chat.postMessage")
    assert replies and all(a.get("thread_ts") == "1700000350.000450" for a in replies)
    session = await bolt.stack.store.get_session("slack_C1_1700000350_000450")
    assert session is not None
    assert "deploy job keeps timing out" in (session.conversation_history[0].content or "")


@pytest.mark.asyncio
async def test_opening_the_app_home_publishes_a_view(bolt: BoltStack) -> None:
    await bolt.dispatch(payload("event_app_home_opened"))
    published = bolt.client.sent("views.publish")
    assert published and published[0]["user_id"] == "U1"
    assert published[0]["view"]["type"] == "home"
    assert "Not linked" in json.dumps(published[0]) or "not linked" in json.dumps(published[0])


@pytest.mark.asyncio
async def test_the_stop_button_only_obeys_the_person_who_asked(bolt: BoltStack) -> None:
    model = bolt.stack.model
    model.gate = asyncio.Event()
    cancelled: list[str] = []

    async def cancel_turn(session_id: str) -> None:
        cancelled.append(session_id)

    bolt.assistant.orchestrator.cancel_turn = cancel_turn  # type: ignore[method-assign]
    turn = asyncio.create_task(bolt.dispatch(payload("event_app_mention")))
    await asyncio.wait_for(model.started.wait(), 5)
    await _wait_for(lambda: "slack_C1_1700000000_000100" in bolt.assistant._active_turns)

    stranger = payload("block_action_stop")
    stranger["user"] = {"id": "U_STRANGER", "team_id": "T1"}
    await bolt.dispatch(stranger)
    assert cancelled == []

    await bolt.dispatch(payload("block_action_stop", trigger_id="TRIGGER_STOP_2"))
    assert cancelled == ["slack_C1_1700000000_000100"]

    model.gate.set()
    await asyncio.wait_for(turn, 10)
