"""WebSockets must see their sessions when PostgreSQL enforces row-level security.

The tenant middleware only covers HTTP requests, so a socket handler has to bind the caller's
organization itself; otherwise every session looks like "not found" and the cockpit never
streams. In-memory stores hide this, so the check needs PostgreSQL.
"""

from __future__ import annotations

import os
import uuid

import pytest
from api.main import app
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs PostgreSQL")

HEADERS = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "member_user",
    "X-User-Role": "developer",
}


def test_the_session_socket_serves_a_session_stored_behind_rls() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/v1/sessions", json={"title": f"ws-{uuid.uuid4().hex[:6]}"}, headers=HEADERS
        )
        assert created.status_code == 200, created.text
        session_id = created.json()["session_id"]

        with client.websocket_connect(f"/v1/sessions/{session_id}/ws", headers=HEADERS) as ws:
            first = ws.receive_json()

    assert first["type"] == "sync" and first["session_id"] == session_id


def test_another_organizations_socket_still_cannot_see_it() -> None:
    other = {**HEADERS, "X-Tenant-Org-Id": "usage_org", "X-Tenant-User-Id": "usage_user"}
    with TestClient(app) as client:
        created = client.post("/v1/sessions", json={"title": "mine"}, headers=HEADERS)
        session_id = created.json()["session_id"]

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/v1/sessions/{session_id}/ws", headers=other) as ws:
                ws.receive_json()


def test_a_stored_question_is_shown_again_and_answering_it_continues_the_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.db.store import TurnTraceStore
    from api.services.turn_trace import TurnTraceAccumulator, begin_turn_trace
    from api.sockets import session_socket

    question = {
        "question_id": "q_restart",
        "question_text": "Which repository should I clone?",
        "options": ["a/one", "b/two"],
        "is_multi_select": False,
        "default_recommended_option": None,
    }
    started: list[str] = []
    monkeypatch.setattr(
        session_socket, "_spawn_background_turn", lambda **kw: started.append(kw["prompt"])
    )

    with TestClient(app) as client:
        created = client.post("/v1/sessions", json={"title": "asked"}, headers=HEADERS)
        session_id = created.json()["session_id"]
        db = client.app.state.db_manager  # type: ignore[attr-defined]

        async def store_question() -> None:
            trace = TurnTraceAccumulator(session_id=session_id, org_id="test_org", turn_index=1)
            await begin_turn_trace(db, trace)
            await TurnTraceStore(db).set_pending_decision(trace.trace_id, "test_org", question)

        # The connection pool belongs to the server's event loop, so use that loop.
        client.portal.call(store_question)  # type: ignore[union-attr]

        with client.websocket_connect(f"/v1/sessions/{session_id}/ws", headers=HEADERS) as ws:
            sync = ws.receive_json()
            assert sync["pending_decision"] == question, "the question survives the restart"

            ws.send_json(
                {
                    "type": "answer_question",
                    "question_id": "q_restart",
                    "selected_options": ["b/two"],
                    "custom_text": "the private one",
                }
            )
            for _ in range(20):
                frame = ws.receive_json()
                if frame.get("event_type") == "decision_submitted":
                    break

        async def remaining() -> object:
            return await TurnTraceStore(db).latest_pending_decision(session_id, "test_org")

        assert client.portal.call(remaining) is None  # type: ignore[union-attr]

    assert len(started) == 1
    assert "Which repository should I clone?" in started[0] and "b/two" in started[0]
    assert "the private one" in started[0]
