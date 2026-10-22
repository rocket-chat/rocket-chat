"""What a turn did before its process died, kept in the session and given back to the agent.

A running turn holds its answer, reasoning and tool calls in memory and writes them into the
session history when it ends. A restart in the middle (a deploy, a drained node, a crash) used to
leave the history with only the person's message: the cockpit showed an empty conversation for a
task that had run for an hour, while everything the agent needs was already saved elsewhere. Each
completed tool call, with the reasoning that led to it, is on the turn's trace, and every message
the model produced is in the transcript. This rebuilds the turn's entry from both, so the work is
shown, and the resumed turn is told exactly where the previous one stopped.

Idempotent per trace: the session remembers which interrupted traces it has already saved.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from api.db.session import DatabaseSessionManager
from api.db.store import TurnTraceRecord, TurnTraceStore
from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol
from specifications.interfaces.transcript import TranscriptStoreProtocol

logger = logging.getLogger(__name__)

SAVED_KEY = "interrupted_turns_saved"
# Only recent interruptions are looked at; older ones were handled by an earlier pass or are
# the history of a session nobody is waiting on.
LOOKBACK_SECONDS = 24 * 3600
# A flag younger than this may belong to a turn that is only now starting.
STALE_FLAG_SECONDS = 120
NOTICE_ARGUMENT_CHARS = 300
NOTICE_REASONING_CHARS = 1200
NOTICE_RECENT_CALLS = 4
NO_RESULT = "No result: the server restarted while this tool was running. Check what it did."

INTERRUPTED_NOTE = (
    "⚠️ The server restarted while the agent was working. What it had done is shown above; "
    "the conversation and the workspace were kept."
)


@dataclass
class InterruptedWork:
    """The part of a turn that was saved before its process stopped."""

    trace_id: str
    executed: list[dict[str, Any]] = field(default_factory=list)
    in_flight: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    reasoning: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.executed or self.in_flight or self.text.strip() or self.reasoning.strip())


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {"raw": str(raw)[:NOTICE_ARGUMENT_CHARS]}
    return parsed if isinstance(parsed, dict) else {}


def _called(message: ChatMessage) -> list[dict[str, Any]]:
    """The calls an assistant message made, as ``{id, name, arguments}``."""
    calls = []
    for call in message.tool_calls or []:
        function = call.get("function") or {}
        calls.append(
            {
                "id": str(call.get("id") or ""),
                "name": str(function.get("name") or call.get("name") or ""),
                "arguments": _arguments(function.get("arguments", call.get("arguments"))),
            }
        )
    return calls


def _saved_call_ids(history: Sequence[ChatMessage]) -> set[str]:
    """Calls the history already holds (a message sent mid-turn saves the turn so far)."""
    return {
        str(call.get("id"))
        for message in history
        if message.role == "assistant"
        for call in message.tool_calls or []
    }


def build_interrupted_work(
    trace: TurnTraceRecord,
    transcript: Sequence[ChatMessage],
    history: Sequence[ChatMessage] = (),
) -> InterruptedWork:
    """The turn's saved work: finished calls from its trace, the rest from the transcript."""
    already = _saved_call_ids(history)
    executed = sorted(
        (call for call in trace.tool_calls if str(call.get("id")) not in already),
        key=lambda call: (call.get("seq") or 0, call.get("created_at") or 0),
    )
    done = {str(call.get("id")) for call in trace.tool_calls} | already
    work = InterruptedWork(trace_id=trace.id, executed=[dict(call) for call in executed])
    start, end = trace.started_at - 1, (trace.ended_at or int(time.time())) + 1
    texts: list[str] = []
    for message in transcript:
        if message.role != "assistant" or not start <= message.created_at <= end:
            continue
        calls = _called(message)
        if calls and all(call["id"] in already for call in calls):
            continue
        if message.content and message.content.strip():
            texts.append(message.content.strip())
        pending = [call for call in calls if call["id"] not in done]
        for call in pending:
            work.in_flight.append({**call, "result": NO_RESULT, "status": "interrupted"})
        if pending and message.reasoning:
            work.in_flight[-len(pending)]["reasoning"] = message.reasoning
        elif not calls and message.reasoning:
            work.reasoning = message.reasoning
    work.text = "\n\n".join(texts)
    return work


def history_entries(work: InterruptedWork) -> list[ChatMessage]:
    """The assistant entry and the note the cockpit shows for an interrupted turn."""
    calls = [*work.executed, *work.in_flight]
    entries: list[ChatMessage] = []
    if not work.is_empty:
        entries.append(
            ChatMessage(
                role="assistant",
                content=work.text,
                reasoning=work.reasoning or None,
                tool_calls=calls or None,
            )
        )
    entries.append(ChatMessage(role="system", content=INTERRUPTED_NOTE))
    return entries


def _brief(arguments: Any) -> str:
    text = json.dumps(arguments, ensure_ascii=False, default=str)
    return text if len(text) <= NOTICE_ARGUMENT_CHARS else text[: NOTICE_ARGUMENT_CHARS - 1] + "…"


def resume_notice(base: str, work: InterruptedWork | None) -> str:
    """The follow-up turn's instruction: the generic one plus where the last turn stopped.

    Kept short on purpose (everything here is re-sent on every step of a turn that may run for
    hours): the agent's own transcript already holds the detail, this only points at it.
    """
    if work is None or work.is_empty:
        return base
    lines = [base]
    if work.executed:
        recent = ", ".join(
            f"{call.get('name')} ({call.get('status', 'completed')})"
            for call in work.executed[-NOTICE_RECENT_CALLS:]
        )
        lines.append(
            f"Before it stopped you had completed {len(work.executed)} tool calls; the last: {recent}."
        )
    for call in work.in_flight:
        lines.append(
            f"Stopped while running `{call.get('name')}` with {_brief(call.get('arguments'))}: "
            "it has no result, so check whether it took effect before repeating it."
        )
    thought = work.reasoning or next(
        (str(call["reasoning"]) for call in reversed(work.in_flight) if call.get("reasoning")), ""
    )
    if thought:
        tail = thought[-NOTICE_REASONING_CHARS:]
        lines.append(f"Your last reasoning before the restart ended with: …{tail}")
    return "\n".join(lines)


async def gather_interrupted_work(
    trace: TurnTraceRecord,
    session: SessionRecord,
    transcripts: TranscriptStoreProtocol | None,
) -> InterruptedWork:
    messages = (
        await transcripts.load(trace.session_id, trace.org_id) if transcripts is not None else []
    )
    return build_interrupted_work(trace, messages, session.conversation_history)


async def save_interrupted_turns(
    admin_manager: DatabaseSessionManager,
    sessions: SessionStoreProtocol,
    transcripts: TranscriptStoreProtocol | None,
    *,
    since: int | None = None,
) -> int:
    """Put the work of recently interrupted turns into their sessions; returns how many were saved."""
    traces = TurnTraceStore(admin_manager)
    cutoff = since if since is not None else int(time.time()) - LOOKBACK_SECONDS
    saved = 0
    for trace in await traces.interrupted_since(cutoff):
        try:
            if await _save_one(trace, sessions, transcripts, traces):
                saved += 1
        except Exception:
            logger.exception("Could not save the work of interrupted turn %s", trace.id)
    if saved:
        logger.warning("Saved the work of %d interrupted turn(s) into their sessions", saved)
    return saved


async def _save_one(
    trace: TurnTraceRecord,
    sessions: SessionStoreProtocol,
    transcripts: TranscriptStoreProtocol | None,
    traces: TurnTraceStore,
) -> bool:
    session = await sessions.get_session(trace.session_id, trace.org_id)
    if session is None or trace.id in (session.metadata or {}).get(SAVED_KEY, []):
        return False
    work = await gather_interrupted_work(trace, session, transcripts)
    entries = history_entries(work)
    # The turn is over, whatever its flag says; a turn running now (a resumed one) keeps it.
    running_now = await traces.running_turn(trace.session_id, trace.org_id) is not None

    def keep(record: SessionRecord) -> None:
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        saved = list(metadata.get(SAVED_KEY, []))
        if trace.id in saved:
            return
        record.conversation_history.extend(entries)
        metadata[SAVED_KEY] = [*saved, trace.id][-50:]
        record.metadata = metadata
        if not running_now:
            record.is_processing = False

    await sessions.mutate_session(trace.session_id, trace.org_id, keep)
    return True


async def clear_stuck_processing(
    admin_manager: DatabaseSessionManager, sessions: SessionStoreProtocol
) -> int:
    """Clear the busy flag of sessions with no running turn, so messages are not queued for nothing."""
    stuck = await TurnTraceStore(admin_manager).sessions_stuck_processing(
        older_than=int(time.time()) - STALE_FLAG_SECONDS
    )

    def clear(record: SessionRecord) -> None:
        record.is_processing = False

    for org_id, session_id in stuck:
        await sessions.mutate_session(session_id, org_id, clear)
    if stuck:
        logger.warning("Cleared the busy flag of %d session(s) with no running turn", len(stuck))
    return len(stuck)
