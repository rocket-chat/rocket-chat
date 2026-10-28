"""The turns this process runs, and what a tab connecting while one runs is shown of it."""

from __future__ import annotations

from typing import Any, Protocol

from specifications.interfaces.agent import RunningTurnLive


class _LiveSource(Protocol):
    async def live_snapshot(self) -> RunningTurnLive: ...


# Session id -> its running turn: turn_index, started_at, model, status, steps (the turn's
# executed tools, shared with its state, so recorded steps appear as they complete) and
# handlers (the turn's event handlers, which know what it has streamed since).
_active_running_turns: dict[str, dict[str, Any]] = {}


async def live_part(session_id: str) -> RunningTurnLive | None:
    """What the turn this process runs for the session streamed since its last step."""
    running = _active_running_turns.get(session_id)
    handlers: _LiveSource | None = running.get("handlers") if running else None
    if handlers is None:
        return None
    return await handlers.live_snapshot()


async def running_turn_snapshot(session_id: str) -> dict[str, Any] | None:
    """The turn this process runs for the session, as the ``sync`` frame's ``running_turn``."""
    running = _active_running_turns.get(session_id)
    if running is None:
        return None
    live = await live_part(session_id)
    snapshot: dict[str, Any] = {
        "turn_index": running["turn_index"],
        "started_at": running["started_at"],
        "model": running.get("model") or "",
        "status": running.get("status") or "running",
        "steps": list(running.get("steps") or []),
        "current_reasoning": "",
        "current_text": "",
    }
    if live is not None:
        snapshot.update(
            current_reasoning=live["current_reasoning"],
            current_text=live["current_text"],
            seq=live["seq"],
        )
    return snapshot
