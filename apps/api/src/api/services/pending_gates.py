"""Which human gates a session is still waiting on, derived from its event history.

A person may open (or reload) a session while its turn is paused for approval. The approval
request was published once, before they connected, so without help the card never appears and
the turn waits forever. The bus history is the one record every replica shares, so the pending
gate is recovered from it rather than from a replica's in-memory state.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from specifications.interfaces.events import EventType, SessionEvent

# Status payloads that mean the turn is no longer paused on a person.
_RESUMED_STATES = frozenset({"running"})
_TURN_OVER = frozenset({"completed", "error", "cancelled"})


@dataclass(frozen=True)
class PendingGates:
    approval: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None


def pending_gates(events: Iterable[SessionEvent]) -> PendingGates:
    approval: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    for event in events:
        kind, payload = event.event_type, event.payload
        if kind is EventType.APPROVAL_REQUIRED:
            # A policy that decided on the organization's behalf never waits for a person.
            if payload.get("policy_decision", "require_human") == "require_human":
                approval = dict(payload)
        elif kind is EventType.DECISION_REQUIRED:
            decision = dict(payload)
        elif kind is EventType.DECISION_SUBMITTED:
            decision = None
        elif kind is EventType.STATUS_CHANGED:
            if payload.get("state") in _RESUMED_STATES or payload.get("status") in _TURN_OVER:
                approval = decision = None
            elif payload.get("status") == "running":
                approval = decision = None  # a new turn started: older gates were abandoned
    return PendingGates(approval=approval, decision=decision)
