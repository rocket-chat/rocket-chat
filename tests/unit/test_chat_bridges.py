"""Teams and Mattermost through one bridge: the turn, its progress, gates and answer reach the
thread; strangers' tokens and unknown Bot Framework hosts are refused."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from api.events import AsyncIOEventBus
from chat_bridges.bridge import DENIED_TOOLS, BridgeCallbacks, ChatBridge, _split
from chat_bridges.mattermost import MattermostPlatform
from chat_bridges.teams import TeamsAuthError, TeamsPlatform, service_url_allowed

from specifications.interfaces.chat import IncomingChatMessage
from specifications.interfaces.events import EventType, SessionEvent


class _Platform:
    name = "mattermost"
    max_message_chars = 500

    def __init__(self) -> None:
        self.replies: list[str] = []
        self.edits: list[str] = []

    async def reply(self, conversation_id: str, thread_id: str, text: str) -> str:
        self.replies.append(text)
        return f"m{len(self.replies)}"

    async def edit(self, conversation_id: str, message_id: str, text: str) -> None:
        self.edits.append(text)

    def permalink(self, message: IncomingChatMessage) -> str | None:
        return None


def _message(text: str = "fix the build", user: str = "u1") -> IncomingChatMessage:
    return IncomingChatMessage("mattermost", "c1", "t1", "p1", user, "Ada", text)


def _bridge(script: Any, **overrides: Any) -> tuple[ChatBridge, _Platform, dict[str, Any]]:
    bus = AsyncIOEventBus()
    seen: dict[str, Any] = {"approvals": [], "answers": []}

    async def run_turn(**kwargs: Any) -> None:
        seen["turn"] = kwargs
        await script(bus, kwargs["session_id"])

    async def approve(session_id: str, gate: str, approved: bool) -> None:
        seen["approvals"].append((gate, approved))

    async def answer(session_id: str, gate: str, options: list[str]) -> None:
        seen["answers"].append((gate, options))

    fields: dict[str, Any] = {
        "guest": AsyncMock(return_value="mattermost:t:u1"),
        "ensure_session": AsyncMock(),
        "run_turn": run_turn,
        "hold_message": AsyncMock(return_value=False),
        "submit_approval": approve,
        "submit_answer": answer,
        "stored_answer": AsyncMock(return_value="All fixed."),
        "event_bus": bus,
        "org_id": "org",
        "web_base_url": "https://rocket.example",
    }
    callbacks = BridgeCallbacks(**{**fields, **overrides})
    platform = _Platform()
    return ChatBridge(platform, callbacks), platform, seen


async def _publish(
    bus: AsyncIOEventBus, session_id: str, kind: EventType, payload: dict[str, Any]
) -> None:
    await bus.publish(SessionEvent(session_id=session_id, event_type=kind, payload=payload))


async def test_a_turn_runs_as_the_guest_without_publishing_and_its_answer_replaces_the_placeholder() -> (
    None
):
    async def script(bus: AsyncIOEventBus, session_id: str) -> None:
        await _publish(
            bus, session_id, EventType.TOOL_STARTED, {"name": "bash_exec", "arguments": {}}
        )
        await _publish(
            bus, session_id, EventType.STATUS_CHANGED, {"status": "completed", "final": True}
        )

    bridge, platform, seen = _bridge(script)
    await bridge.handle(_message())
    assert platform.replies[0] == "Working on it…"
    assert "bash exec" in platform.edits[0]
    assert (
        platform.edits[-1].startswith("All fixed.") and "rocket.example/chat/" in platform.edits[-1]
    )
    assert seen["turn"]["author_user_id"] == "mattermost:t:u1"
    assert seen["turn"]["overlay"].denied_tools == DENIED_TOOLS
    assert seen["turn"]["prompt"].startswith("[Speaker: Ada]")


async def test_the_requester_answers_an_approval_in_the_thread() -> None:
    gate_seen = asyncio.Event()

    async def script(bus: AsyncIOEventBus, session_id: str) -> None:
        await _publish(
            bus,
            session_id,
            EventType.APPROVAL_REQUIRED,
            {
                "action_id": "a1",
                "action_type": "bash_exec",
                "command_or_path": "rm -rf build",
                "risk_level": "high",
            },
        )
        gate_seen.set()
        await asyncio.sleep(0.2)
        await _publish(bus, session_id, EventType.STATUS_CHANGED, {"final": True})

    # While the turn runs, another message is queued behind it, as the pipeline does.
    bridge, platform, seen = _bridge(script, hold_message=AsyncMock(side_effect=[False, True]))
    task = asyncio.create_task(bridge.handle(_message()))
    await gate_seen.wait()
    for _ in range(50):  # the bridge renders the gate on its own task
        if any("Approval needed" in r for r in platform.replies):
            break
        await asyncio.sleep(0.001)
    assert any("Approval needed" in r for r in platform.replies)
    await bridge.handle(
        _message("approve", user="intruder")
    )  # someone else: a new request, not an answer
    assert seen["approvals"] == []
    await bridge.handle(_message("approve"))
    await task
    assert seen["approvals"] == [("a1", True)]


async def test_a_failed_turn_never_shows_the_raw_error() -> None:
    async def script(bus: AsyncIOEventBus, session_id: str) -> None:
        await _publish(bus, session_id, EventType.ERROR, {"error": "connect 10.0.0.4 refused"})
        await _publish(bus, session_id, EventType.STATUS_CHANGED, {"final": True})

    bridge, platform, _ = _bridge(script)
    await bridge.handle(_message())
    assert "Something went wrong" in platform.edits[-1] and "10.0.0.4" not in platform.edits[-1]


async def test_a_person_is_rate_limited() -> None:
    async def script(bus: AsyncIOEventBus, session_id: str) -> None:
        await _publish(bus, session_id, EventType.STATUS_CHANGED, {"final": True})

    bridge, platform, _ = _bridge(script)
    bridge._clock = lambda: 100.0
    for _ in range(7):
        await bridge.handle(_message())
    assert platform.replies[-1].startswith("Too many requests")


def test_long_answers_are_split_at_line_ends() -> None:
    parts = _split("a" * 30 + "\n" + "b" * 40, 50)
    assert parts == ["a" * 30, "b" * 40]


def test_mattermost_takes_direct_messages_mentions_and_its_threads_only() -> None:
    platform = MattermostPlatform("https://mm.example", "t", client=AsyncMock())
    platform.bot_user_id, platform.bot_username = "bot", "rocket"

    def event(
        message: str, channel_type: str = "O", root: str = "", user: str = "u1"
    ) -> dict[str, Any]:
        post = {
            "id": "p9",
            "user_id": user,
            "channel_id": "c1",
            "root_id": root,
            "message": message,
        }
        return {
            "event": "posted",
            "data": {"post": json.dumps(post), "channel_type": channel_type, "sender_name": "@ada"},
        }

    never = lambda *_: False  # noqa: E731
    assert platform.message_of(event("hello"), never) is None
    mention = platform.message_of(event("@rocket fix it"), never)
    assert mention is not None and mention.text == "fix it" and mention.thread_id == "p9"
    assert platform.message_of(event("hi", channel_type="D"), never) is not None
    assert platform.message_of(event("more", root="r1"), lambda c, r: r == "r1") is not None
    assert platform.message_of(event("@rocket loop", user="bot"), never) is None


def test_teams_replies_only_to_bot_framework_hosts() -> None:
    assert service_url_allowed("https://smba.trafficmanager.net/emea/")
    assert not service_url_allowed("https://evil.example/botframework.com")
    assert not service_url_allowed("http://smba.trafficmanager.net/")
    platform = TeamsPlatform("app", "secret", client=AsyncMock())
    activity = {
        "type": "message",
        "serviceUrl": "https://evil.example/",
        "conversation": {"id": "c"},
    }
    assert platform.message_of(activity) is None
    good = {**activity, "serviceUrl": "https://smba.trafficmanager.net/emea/", "id": "a1", "text": "<at>Rocket</at> help",
            "entities": [{"type": "mention", "text": "<at>Rocket</at>"}], "from": {"aadObjectId": "u", "name": "Ada"}}  # fmt: skip
    message = platform.message_of(good)
    assert message is not None and message.text == "help" and message.thread_id == "a1"


async def test_teams_refuses_a_request_without_a_valid_token() -> None:
    platform = TeamsPlatform("app", "secret", client=AsyncMock())
    with pytest.raises(TeamsAuthError):
        await platform.verify(None)
    with pytest.raises(TeamsAuthError):
        await platform.verify("Bearer not-a-jwt")


def test_the_teams_endpoint_is_off_without_configuration(client: Any) -> None:
    assert client.post("/v1/chat/teams/messages", json={}).status_code == 404
