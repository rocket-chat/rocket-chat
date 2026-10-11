"""A tab that connects while a turn waits on a person still gets the card."""

from __future__ import annotations

import asyncio

import pytest
from api.main import app
from api.services.pending_gates import pending_gates
from fastapi.testclient import TestClient

from specifications.interfaces.events import EventType, SessionEvent


def _event(kind: EventType, **payload: object) -> SessionEvent:
    return SessionEvent(session_id="s", event_type=kind, payload=dict(payload))


APPROVAL = _event(
    EventType.APPROVAL_REQUIRED,
    action_id="ap_1",
    action_type="bash_exec",
    policy_decision="require_human",
)


def test_an_unanswered_approval_is_pending() -> None:
    gates = pending_gates([_event(EventType.LOG_CHUNK, text="x"), APPROVAL])
    assert gates.approval is not None and gates.approval["action_id"] == "ap_1"
    assert gates.decision is None


def test_it_stays_pending_while_other_tools_keep_producing_output() -> None:
    gates = pending_gates([APPROVAL, _event(EventType.LOG_CHUNK, text="parallel tool output")])
    assert gates.approval is not None


@pytest.mark.parametrize(
    "resolution",
    [
        _event(EventType.STATUS_CHANGED, state="running"),
        _event(EventType.STATUS_CHANGED, status="completed"),
        _event(EventType.STATUS_CHANGED, status="cancelled"),
        _event(EventType.STATUS_CHANGED, status="error"),
    ],
)
def test_resuming_or_ending_the_turn_clears_it(resolution: SessionEvent) -> None:
    assert pending_gates([APPROVAL, resolution]).approval is None


def test_an_approval_the_policy_already_decided_never_waits_for_a_person() -> None:
    decided = _event(EventType.APPROVAL_REQUIRED, action_id="ap_2", policy_decision="auto_approve")
    assert pending_gates([decided]).approval is None


def test_a_question_is_pending_until_it_is_answered() -> None:
    question = _event(EventType.DECISION_REQUIRED, question_id="q1", question="Which?")
    assert pending_gates([question]).decision is not None
    answered = _event(EventType.DECISION_SUBMITTED, question_id="q1")
    assert pending_gates([question, answered]).decision is None


def test_a_newer_turn_abandons_older_gates() -> None:
    new_turn = _event(
        EventType.STATUS_CHANGED, status="running", message="Analyzing instruction..."
    )
    assert pending_gates([APPROVAL, new_turn]).approval is None


HEADERS = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "gate_user",
    "X-User-Role": "developer",
}


def test_the_websocket_sync_frame_carries_the_pending_gate() -> None:
    with TestClient(app) as client:
        app.state.db_manager = None
        created = client.post("/v1/sessions", json={"title": "gated"}, headers=HEADERS).json()
        session_id = created["session_id"]
        asyncio.run(
            app.state.event_bus.publish(
                SessionEvent(
                    session_id=session_id,
                    event_type=EventType.APPROVAL_REQUIRED,
                    payload={
                        "action_id": "ap_late",
                        "action_type": "bash_exec",
                        "command_or_path": "bash_exec: make",
                        "risk_level": "medium",
                        "policy_decision": "require_human",
                    },
                )
            )
        )
        with client.websocket_connect(f"/v1/sessions/{session_id}/ws", headers=HEADERS) as ws:
            sync = ws.receive_json()

    assert sync["type"] == "sync"
    assert sync["pending_approval"]["action_id"] == "ap_late"
    assert sync["pending_decision"] is None
