"""Every turn of a Slack thread reaches it, not only the ones a Slack message started.

A CI wake, a goal continuation, a GitHub event, a resumed turn or a message sent from the web
app on a Slack session runs the same shared turn; the bot's delivery renders it into the
session's stored thread.
"""

from __future__ import annotations

import json

import pytest
from test_slack_bolt_contract import BoltStack, payload
from test_slack_shared_pipeline import _wait_for

from specifications.interfaces.agent import TurnOverlay


def _answers(bolt: BoltStack, text: str) -> int:
    return sum(text in json.dumps(args) for args in bolt.client.sent("chat.postMessage"))


async def _slack_session(bolt: BoltStack) -> tuple[str, str]:
    await bolt.dispatch(payload("event_app_mention"))
    await bolt.settle()
    session_id = bolt.assistant.get_session_id("C1", "1700000000.000100")
    record = await bolt.stack.store.get_session(session_id)
    assert record is not None
    return session_id, record.tenant_org_id


@pytest.fixture
def bolt(monkeypatch: pytest.MonkeyPatch) -> BoltStack:
    return BoltStack(monkeypatch)


@pytest.mark.asyncio
async def test_a_turn_started_outside_slack_is_posted_in_its_thread(bolt: BoltStack) -> None:
    session_id, org_id = await _slack_session(bolt)
    assert _answers(bolt, "Fixed it.") == 1
    bolt.assistant._start_delivery({org_id})
    run_turn = bolt.assistant._run_turn_fn
    assert run_turn is not None

    # As a CI wake or a message from the web app: no Slack request waits on this turn.
    await run_turn(
        session_id=session_id,
        prompt="CI failed on the last push: fix it",
        overlay=TurnOverlay(),
        author_user_id="slack_global",
    )

    await _wait_for(lambda: _answers(bolt, "Fixed it.") == 2)
    posted = bolt.client.sent("chat.postMessage")
    assert all(args.get("thread_ts") == "1700000000.000100" for args in posted)
    bolt.assistant._stop_delivery()


@pytest.mark.asyncio
async def test_a_thread_another_bot_owns_is_left_to_it(bolt: BoltStack) -> None:
    session_id, org_id = await _slack_session(bolt)

    def owned_elsewhere(record: object) -> None:
        record.metadata["slack_bot_id"] = "another-bot"  # type: ignore[attr-defined]

    await bolt.stack.store.mutate_session(session_id, org_id, owned_elsewhere)
    bolt.assistant._start_delivery({org_id})
    run_turn = bolt.assistant._run_turn_fn
    assert run_turn is not None

    await run_turn(
        session_id=session_id, prompt="again", overlay=TurnOverlay(), author_user_id="slack_global"
    )
    await bolt.settle()

    assert _answers(bolt, "Fixed it.") == 1
    bolt.assistant._stop_delivery()
