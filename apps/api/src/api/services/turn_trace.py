"""Turn-trace assembly for durable session continuity.

The orchestrator streams a turn as events and the API layer already accumulates the
pieces that outlive the stream (executed tools, file diffs, approval gates). This
module turns exactly those accumulators into one trace row, so a completed — or
failed — turn can be inspected after the fact instead of vanishing with the socket.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from agent_core.tools.shell_intent import shell_intents

from api.db.session import DatabaseSessionManager
from api.db.store import (
    TURN_STATUS_COMPLETED,
    TURN_STATUS_ERROR,
    TURN_STATUS_INTERRUPTED,
    TURN_STATUS_RUNNING,
    TurnTraceRecord,
    TurnTraceStore,
)

logger = logging.getLogger(__name__)

__all__ = [
    "INTERRUPTED_TURN_ERROR",
    "LEASE_HEARTBEAT_SECONDS",
    "LEASE_TTL_SECONDS",
    "MAX_REASONING_SUMMARY_CHARS",
    "PROCESS_INSTANCE_ID",
    "TURN_STATUS_COMPLETED",
    "TURN_STATUS_ERROR",
    "TURN_STATUS_INTERRUPTED",
    "TURN_STATUS_RUNNING",
    "TurnMetrics",
    "TurnTraceAccumulator",
    "active_turn_sessions",
    "beat_turn_leases",
    "begin_turn_trace",
    "persist_turn_trace",
    "recover_interrupted_turns",
    "session_turn_activity",
    "user_turn_index",
]

# Identifies this process in the turn leases it holds; a restarted process gets a new one, so
# its predecessor's turns are recognised as orphaned.
PROCESS_INSTANCE_ID = uuid.uuid4().hex[:16]
# A replica proves it is alive every few seconds; a turn whose owner has been silent for the
# TTL (several missed beats) is considered orphaned and recoverable by any peer.
LEASE_HEARTBEAT_SECONDS = 10
LEASE_TTL_SECONDS = 45

# The terminal error recorded for a turn whose process died before it could finish. It is
# the only signal the cockpit has to explain why a turn has no result and to offer a retry.
INTERRUPTED_TURN_ERROR = "Turn interrupted: the API process stopped before the turn completed."

# Reasoning is anchored context, not a transcript: the cockpit turn inspector only
# needs the shape of the plan, so the summary is bounded (deterministic head clamp)
# to keep one turn's traces small.
MAX_REASONING_SUMMARY_CHARS = 2000
_TRUNCATION_MARKER = "\n… [reasoning truncated]"


# Tools whose call publishes commits to a remote: each one starts a CI run the agent may wait on.
_PUBLISHING_TOOLS = frozenset({"git_push", "git_open_pr"})


@dataclass
class TurnMetrics:
    """How a turn spent its time and context, beside its tokens and cost.

    These are the figures that tell a slow turn from an expensive one: how many model steps,
    how long the model generated versus how long tools ran, how much it reasoned (and the
    longest single deliberation), what every request paid before the conversation, and how
    often the context was compacted with a summary the model did not write.
    """

    steps: int = 0
    model_ms: int = 0
    tool_ms: int = 0
    tool_calls: int = 0
    reasoning_chars: int = 0
    max_step_reasoning_chars: int = 0
    reasoning_limit_stops: int = 0
    overhead_tokens: int = 0
    overhead_system_tokens: int = 0
    overhead_tool_tokens: int = 0
    overhead_tools_sent: int = 0
    context_window: int = 0
    # 'override', 'provider', 'litellm', 'family' or 'default' (a guess); empty before a call.
    context_window_source: str = ""
    shell_file_operations: int = 0
    tool_loads: int = 0
    pushes: int = 0
    compactions: int = 0
    compactions_without_model_summary: int = 0

    def add_model_call(self, payload: Mapping[str, Any]) -> None:
        if str(payload.get("role") or "") != "react":
            return
        self.steps += 1
        self.model_ms += int(payload.get("duration_ms") or 0)
        reasoning = int(payload.get("reasoning_chars") or 0)
        self.reasoning_chars += reasoning
        self.max_step_reasoning_chars = max(self.max_step_reasoning_chars, reasoning)
        if payload.get("finish_reason") == "reasoning_limit":
            self.reasoning_limit_stops += 1
        self.overhead_tokens = max(self.overhead_tokens, int(payload.get("overhead_tokens") or 0))
        for name in ("overhead_system_tokens", "overhead_tool_tokens", "overhead_tools_sent"):
            setattr(self, name, max(getattr(self, name), int(payload.get(name) or 0)))
        self.context_window = int(payload.get("context_window") or self.context_window)
        self.context_window_source = str(
            payload.get("context_window_source") or self.context_window_source
        )

    def add_tool_call(self, tool_call: Mapping[str, Any]) -> None:
        self.tool_calls += 1
        self.tool_ms += int(float(tool_call.get("duration_ms") or 0))
        name = str(tool_call.get("name") or "")
        if name == "bash_exec" and shell_intents(_command_of(tool_call.get("arguments"))):
            self.shell_file_operations += 1
        if name == "load_tools":
            self.tool_loads += 1
        elif name in _PUBLISHING_TOOLS:
            self.pushes += 1

    def add_compaction(self, payload: Mapping[str, Any]) -> None:
        self.compactions += 1
        if payload.get("summary_status") not in ("model", "model_retry"):
            self.compactions_without_model_summary += 1

    def as_dict(self) -> dict[str, int | str]:
        return asdict(self)


def _command_of(arguments: Any) -> str:
    """The ``command`` of a ``bash_exec`` call whose arguments are a dict or a JSON string."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return ""
    return str(arguments.get("command") or "") if isinstance(arguments, dict) else ""


def _bounded_reasoning(reasoning: str | None) -> str | None:
    text = (reasoning or "").strip()
    if not text:
        return None
    if len(text) <= MAX_REASONING_SUMMARY_CHARS:
        return text
    return text[:MAX_REASONING_SUMMARY_CHARS] + _TRUNCATION_MARKER


class TurnTraceAccumulator:
    """Collects everything a turn leaves behind, then freezes it into one record."""

    def __init__(
        self,
        *,
        session_id: str,
        org_id: str,
        turn_index: int,
        started_at: int | None = None,
        resume_attempt: int = 0,
    ) -> None:
        self.session_id = session_id
        self.resume_attempt = resume_attempt
        self.org_id = org_id
        self.turn_index = turn_index
        # The identifier is minted up front so the running marker written at turn start
        # and the terminal write in the turn's `finally` address the same row.
        self.trace_id = f"trace_{uuid.uuid4().hex[:16]}"
        self.started_at = started_at if started_at is not None else int(time.time())
        self._tool_calls: list[dict[str, Any]] = []
        self._diff_paths: list[str] = []
        self._approvals: list[dict[str, Any]] = []
        self._attachments: list[dict[str, Any]] = []
        # Cost attribution lives in the existing `tokens` JSONB column, so
        # no migration is needed: `_routing` is the effective model + routing decision and
        # `_usage` is the per-turn token totals folded from `model_usage` events.
        self._routing: dict[str, Any] | None = None
        self._usage: dict[str, int] = {}
        # Summed in integer micro-dollars so thousands of sub-cent calls do not drift;
        # the trace reports dollars.
        self._cost_micro_usd = 0
        self._usage_by_model: dict[str, dict[str, int]] = {}
        self._model_calls: list[dict[str, Any]] = []
        self._metrics = TurnMetrics()

    def start_record(self) -> TurnTraceRecord:
        """The durable ``running`` marker persisted before the turn does any work."""
        return TurnTraceRecord(
            id=self.trace_id,
            session_id=self.session_id,
            org_id=self.org_id,
            turn_index=self.turn_index,
            started_at=self.started_at,
            ended_at=None,
            status=TURN_STATUS_RUNNING,
            owner_instance=PROCESS_INSTANCE_ID,
            heartbeat_at=self.started_at,
            resume_attempt=self.resume_attempt,
        )

    def record_tool_call(self, tool_call: Mapping[str, Any]) -> None:
        """Record a completed tool call — the same dict published as ``tool_completed``."""
        self._tool_calls.append(dict(tool_call))
        self._metrics.add_tool_call(tool_call)

    def record_compaction(self, payload: Mapping[str, Any]) -> None:
        """Count a context compaction, and whether its summary was the model's own."""
        self._metrics.add_compaction(payload)

    def record_file_diff(self, payload: Mapping[str, Any]) -> None:
        """Record a touched path exactly once, preserving first-touch order."""
        path = str(payload.get("path") or "").strip()
        if path and path not in self._diff_paths:
            self._diff_paths.append(path)

    def record_attachments(self, refs: Sequence[Any]) -> None:
        """Remember which files the turn was given, by reference, never by content."""
        self._attachments = [
            {
                "id": ref.attachment_id,
                "name": ref.name,
                "mime": ref.mime,
                "size": ref.size,
            }
            for ref in refs
        ]

    def record_approval_request(self, payload: Mapping[str, Any]) -> None:
        """Record a human gate the turn paused on, with its risk classification."""
        self._approvals.append(
            {
                "action_id": payload.get("action_id"),
                "action_type": payload.get("action_type"),
                "command_or_path": payload.get("command_or_path"),
                "risk_level": payload.get("risk_level"),
            }
        )

    def set_routing(
        self,
        *,
        effective_model: str,
        mode: str,
        role_models: Mapping[str, str],
    ) -> None:
        """Persist the routing decision so a trace explains which model did what work."""
        self._routing = {
            "effective_model": effective_model,
            "mode": mode,
            "roles": dict(role_models),
        }

    def record_model_usage(self, payload: Mapping[str, Any]) -> None:
        """Fold one ``model_usage`` event into the turn's token totals."""
        prompt = int(payload.get("prompt_tokens") or 0)
        completion = int(payload.get("completion_tokens") or 0)
        cache_read = int(payload.get("cache_read_tokens") or 0)
        cache_creation = int(payload.get("cache_creation_tokens") or 0)
        cost = float(payload.get("cost_usd") or 0.0)
        self._usage["prompt_tokens"] = self._usage.get("prompt_tokens", 0) + prompt
        self._usage["completion_tokens"] = self._usage.get("completion_tokens", 0) + completion
        self._usage["cache_read_tokens"] = self._usage.get("cache_read_tokens", 0) + cache_read
        self._usage["cache_creation_tokens"] = (
            self._usage.get("cache_creation_tokens", 0) + cache_creation
        )
        self._cost_micro_usd += int(cost * 1_000_000)
        self._usage["model_calls"] = self._usage.get("model_calls", 0) + 1

        model = str(payload.get("model") or "unknown")
        bucket = self._usage_by_model.setdefault(
            model,
            {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            },
        )
        bucket["prompt_tokens"] += prompt
        bucket["completion_tokens"] += completion
        bucket["cache_read_tokens"] += cache_read
        bucket["cache_creation_tokens"] += cache_creation

        self._metrics.add_model_call(payload)
        self._model_calls.append(
            {
                "model": model,
                "role": str(payload.get("role") or "assistant"),
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": cache_creation,
                "cost_usd": cost,
                "finish_reason": str(payload.get("finish_reason") or "stop"),
            }
        )

    @property
    def prompt_tokens(self) -> int:
        return self._usage.get("prompt_tokens", 0)

    @property
    def completion_tokens(self) -> int:
        return self._usage.get("completion_tokens", 0)

    @property
    def cost_usd(self) -> float:
        return self._cost_micro_usd / 1_000_000.0

    def finish(
        self,
        *,
        status: str,
        error: str | None = None,
        reasoning: str | None = None,
    ) -> TurnTraceRecord:
        """Freeze the accumulated trace. Call exactly once, in the turn's ``finally``."""
        tokens: dict[str, Any] = {}
        if self._routing is not None:
            tokens.update(self._routing)
        if self._usage:
            tokens["usage"] = {**self._usage, "cost_usd": round(self.cost_usd, 6)}
            prompt = self._usage.get("prompt_tokens", 0)
            # Share of the prompt the provider served from its cache: what the request layout
            # (stable prompt first, rolling breakpoint, volatile context last) is meant to raise.
            tokens["usage"]["cache_hit_ratio"] = (
                round(self._usage.get("cache_read_tokens", 0) / prompt, 4) if prompt else 0.0
            )
            tokens["by_model"] = {
                model: dict(totals) for model, totals in self._usage_by_model.items()
            }
            tokens["calls"] = list(self._model_calls)
        if self._metrics.steps or self._metrics.tool_calls:
            tokens["metrics"] = self._metrics.as_dict()
        return TurnTraceRecord(
            id=self.trace_id,
            session_id=self.session_id,
            org_id=self.org_id,
            turn_index=self.turn_index,
            started_at=self.started_at,
            ended_at=int(time.time()),
            status=status,
            reasoning_summary=_bounded_reasoning(reasoning),
            tool_calls=list(self._tool_calls),
            diff_paths=list(self._diff_paths),
            approvals=list(self._approvals),
            tokens=tokens,
            attachments=list(self._attachments),
            error=error,
            owner_instance=PROCESS_INSTANCE_ID,
            resume_attempt=self.resume_attempt,
        )


def user_turn_index(conversation_history: Sequence[Any]) -> int:
    """Derive the 1-based turn number from the instructions already in the session.

    Counting accepted user messages instead of querying prior traces keeps the index
    stable when two turns of one session overlap: each turn computes its own index at
    submission time, not at write time.
    """
    return sum(1 for message in conversation_history if getattr(message, "role", "") == "user") or 1


async def begin_turn_trace(
    db_manager: DatabaseSessionManager | None,
    accumulator: TurnTraceAccumulator,
) -> None:
    """Write the turn's ``running`` marker, never letting a failure abort the turn.

    A missing marker degrades recovery (this turn would have nothing to interrupt), not
    the turn itself, so the error is logged rather than raised — and ``persist_turn_trace``
    still inserts the terminal row if the begin write was skipped.
    """
    if db_manager is None:
        logger.debug(
            "No database manager configured; turn %s started without a durable marker",
            accumulator.trace_id,
        )
        return
    try:
        await TurnTraceStore(db_manager).begin(accumulator.start_record())
    except Exception:
        logger.exception(
            "Failed to persist running marker %s for session %s",
            accumulator.trace_id,
            accumulator.session_id,
        )


async def persist_turn_trace(
    db_manager: DatabaseSessionManager | None,
    trace: TurnTraceRecord,
) -> None:
    """Finalize one trace, never letting a persistence failure mask the turn's outcome.

    Called from a ``finally``: raising here would replace the original error of a
    failed turn with a secondary one. The turn's chat history remains authoritative,
    so a lost trace is logged loudly rather than propagated.
    """
    if db_manager is None:
        logger.debug("No database manager configured; turn trace %s not persisted", trace.id)
        return
    try:
        await TurnTraceStore(db_manager).complete(trace)
    except Exception:
        logger.exception(
            "Failed to persist turn trace %s for session %s", trace.id, trace.session_id
        )


async def beat_turn_leases(db_manager: DatabaseSessionManager | None) -> int:
    """Refresh the heartbeat of the turns this process is running (administrative connection)."""
    if db_manager is None:
        return 0
    return await TurnTraceStore(db_manager).beat(PROCESS_INSTANCE_ID)


async def recover_interrupted_turns(
    db_manager: DatabaseSessionManager | None, *, stale_after_seconds: int | None = None
) -> int:
    """Recover turns whose process died mid-flight.

    Runs on the administrative connection. With a single replica, call it once at startup
    without ``stale_after_seconds``: every trace still ``running`` belongs to a dead process.
    With several replicas pass the lease TTL, so only turns whose owner stopped heartbeating
    are marked ``interrupted`` and the cockpit can offer a retry instead of the turn silently
    disappearing. Returns the number recovered.
    """
    if db_manager is None:
        logger.debug("No database manager configured; skipping interrupted-turn recovery")
        return 0
    stale_before = (
        int(time.time()) - stale_after_seconds if stale_after_seconds is not None else None
    )
    recovered = await TurnTraceStore(db_manager).mark_running_as_interrupted(
        INTERRUPTED_TURN_ERROR, stale_before=stale_before
    )
    if recovered:
        logger.warning("Recovered %d interrupted turn trace(s)", recovered)
    return recovered


async def active_turn_sessions(db_manager: DatabaseSessionManager | None) -> set[str]:
    """Return session IDs that currently have a turn actively running."""
    if db_manager is None:
        return set()
    try:
        return await TurnTraceStore(db_manager).active_running_sessions()
    except Exception:
        logger.exception("Failed to query active running sessions")
        return set()


async def session_turn_activity(db_manager: DatabaseSessionManager | None) -> dict[str, float]:
    """Return mapping of session ID to most recent turn activity timestamp."""
    if db_manager is None:
        return {}
    try:
        return await TurnTraceStore(db_manager).latest_turn_activity()
    except Exception:
        logger.exception("Failed to query session turn activity")
        return {}


async def record_running_tool_call(
    db_manager: DatabaseSessionManager | None,
    trace_id: str,
    org_id: str,
    tool_data: Mapping[str, Any],
) -> None:
    """Save a completed tool call to the running turn trace in PostgreSQL."""
    if db_manager is None:
        return
    try:
        await TurnTraceStore(db_manager).record_tool_call(trace_id, org_id, tool_data)
    except Exception:
        logger.exception("Failed to record tool call for running trace %s", trace_id)


async def session_running_turn(
    db_manager: DatabaseSessionManager | None,
    session_id: str,
    org_id: str,
) -> TurnTraceRecord | None:
    """Fetch the active running turn trace for a session."""
    if db_manager is None:
        return None
    try:
        return await TurnTraceStore(db_manager).running_turn(session_id, org_id)
    except Exception:
        logger.exception("Failed to query running turn for session %s", session_id)
        return None
