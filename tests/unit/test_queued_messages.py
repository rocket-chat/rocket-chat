"""Messages sent during a turn are kept until a turn takes them: steered, queued, or withdrawn."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from api.services import queued_messages
from api.services.queued_messages import (
    STRANDED_AFTER_SECONDS,
    QueuedMessageStore,
    stranded_sessions,
)

ORG, SESSION = "org_queue", "session_queue"


@pytest.fixture(autouse=True)
def _clean() -> Any:
    queued_messages._memory.clear()
    yield
    queued_messages._memory.clear()


async def _send(store: QueuedMessageStore, message_id: str, delivery: str = "queue") -> None:
    await store.add(ORG, SESSION, message_id, f"text {message_id}", delivery, "u1")  # type: ignore[arg-type]


async def test_the_next_turns_take_what_was_not_taken_in_the_order_it_was_sent() -> None:
    store = QueuedMessageStore(None)
    for message_id in ("m1", "m2", "m3"):
        await _send(store, message_id, "steer" if message_id == "m2" else "queue")
    await store.mark_taken(ORG, SESSION, "m2")
    assert await store.cancel(ORG, SESSION, "m3")

    claimed = await store.claim_for_next_turn(ORG, SESSION)

    assert [m.id for m in claimed] == ["m1"], "the steered one was taken, the third withdrawn"
    assert await store.claim_for_next_turn(ORG, SESSION) == [], "never started twice"
    assert not await store.cancel(ORG, SESSION, "m1"), "too late to withdraw a started one"


async def test_two_claims_at_once_start_each_message_exactly_once() -> None:
    store = QueuedMessageStore(None)
    for n in range(20):
        await _send(store, f"m{n:02d}")

    first, second = await asyncio.gather(
        store.claim_for_next_turn(ORG, SESSION), store.claim_for_next_turn(ORG, SESSION)
    )

    ids = [m.id for m in [*first, *second]]
    assert sorted(ids) == [f"m{n:02d}" for n in range(20)] and len(set(ids)) == 20


async def test_a_message_left_pending_is_found_by_the_sweeper_after_a_while() -> None:
    store = QueuedMessageStore(None)
    sent = await store.add(ORG, SESSION, "m1", "text", "queue", "u1")

    assert await stranded_sessions(None, now=sent.created_at) == []
    later = sent.created_at + STRANDED_AFTER_SECONDS
    assert await stranded_sessions(None, now=later) == [(ORG, SESSION)]


def _headers() -> dict[str, str]:
    return {
        "X-Tenant-Org-Id": "default_org",
        "X-Tenant-User-Id": "dev_user",
        "X-User-Role": "admin",
    }


def test_while_a_turn_runs_a_queued_message_waits_and_can_be_withdrawn() -> None:
    from api.main import app
    from fastapi.testclient import TestClient

    from specifications.interfaces.events import EventType

    with TestClient(app) as client:
        session_id = client.post(
            "/v1/sessions", json={"title": "Queue"}, headers=_headers()
        ).json()["session_id"]

        async def running(*_: Any) -> None:
            def busy(record: Any) -> None:
                record.is_processing = True

            await app.state.session_store.mutate_session(session_id, "default_org", busy)

        asyncio.run(running())
        reply = client.post(
            f"/v1/sessions/{session_id}/turns",
            json={"prompt": "then update the changelog", "delivery": "queue"},
            headers=_headers(),
        ).json()

        assert reply["status"] == "queued"
        history, _ = app.state.event_bus.replay_since(session_id, 0)
        (pending,) = [e for e in history if e.event_type == EventType.MESSAGE_PENDING]
        assert pending.payload["delivery"] == "queue"
        assert pending.payload["message_id"] == reply["message_id"]

        gone = client.delete(
            f"/v1/sessions/{session_id}/queued-messages/{reply['message_id']}", headers=_headers()
        )
        assert gone.status_code == 200
        again = client.delete(
            f"/v1/sessions/{session_id}/queued-messages/{reply['message_id']}", headers=_headers()
        )
        assert again.status_code == 404
        history, _ = app.state.event_bus.replay_since(session_id, 0)
        assert any(e.event_type == EventType.MESSAGE_CANCELLED for e in history)


def test_a_read_only_member_cannot_withdraw_a_message() -> None:
    from api.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        session_id = client.post("/v1/sessions", json={"title": "Q"}, headers=_headers()).json()[
            "session_id"
        ]
        reader = {**_headers(), "X-User-Role": "readonly"}
        reply = client.delete(f"/v1/sessions/{session_id}/queued-messages/x", headers=reader)
        assert reply.status_code in (403, 404)
