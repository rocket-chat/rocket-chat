"""What happens after the model stops: recording the answer, the trace and the follow-ups."""

from __future__ import annotations

import time
from typing import Any

from api.services.memory import MemoryService, write_session_summary
from api.services.metrics import record_turn
from api.services.settings import effective_settings
from api.services.shutdown import is_shutting_down
from api.services.title import apply_ai_title
from api.services.turn_trace import (
    INTERRUPTED_TURN_ERROR,
    TURN_STATUS_COMPLETED,
    TURN_STATUS_ERROR,
    TURN_STATUS_INTERRUPTED,
    persist_turn_trace,
    user_turn_index,
)
from api.turns.context import TurnContext, TurnRequest, TurnState
from api.turns.followup import suggest_followup
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.llm import ChatMessage
from specifications.interfaces.session import SessionRecord


async def record_answer(ctx: TurnContext) -> str:
    """Append the assistant's answer to the session history and announce completion.

    Raises when the orchestrator failed before producing anything usable: that is a failed
    turn, not an empty answer.
    """
    state, req = ctx.state, ctx.req
    content = "".join(state.assistant_parts).strip()
    if state.orchestrator_error:
        if not content:
            raise RuntimeError(state.orchestrator_error)
        state.turn_error = RuntimeError(state.orchestrator_error)
    reasoning = "".join(state.reasoning_parts).strip()

    def append_answer(record: SessionRecord) -> None:
        if state.had_post_failure_synthesis:
            record.conversation_history.append(
                ChatMessage(
                    role="system",
                    content=f"⚡ Turn Budget Exceeded ({state.synthesis_turns} turns) — Synthesizing final answer from gathered context...",
                )
            )
        record.conversation_history.append(
            ChatMessage(
                role="assistant",
                content=content,
                reasoning=reasoning or None,
                tool_calls=state.executed_tools or None,
            )
        )
        if getattr(state, "is_waiting", False):
            wait_msg = getattr(state, "wait_summary", None) or (
                f"Waiting for {getattr(state, 'wait_target', 'external event')}. Durable wait registered; turn paused."
            )
            record.conversation_history.append(
                ChatMessage(
                    role="system",
                    content=wait_msg,
                )
            )

    # Atomic: the title task writes the same record while the turn is finishing.
    await req.store.mutate_session(ctx.session_id, ctx.session.tenant_org_id, append_answer)
    status = "waiting" if getattr(state, "is_waiting", False) else "completed"
    msg = (
        "Paused waiting for external condition."
        if getattr(state, "is_waiting", False)
        else "Agent turn complete."
    )
    await req.event_bus.publish(
        SessionEvent(
            session_id=ctx.session_id,
            event_type=EventType.STATUS_CHANGED,
            payload={"status": status, "message": msg, "final": True},
        )
    )
    return content


async def publish_followup(ctx: TurnContext, answer: str) -> None:
    """Offer the likely next instruction, unless the member turned suggestions off."""
    if getattr(ctx.state, "is_continuation", False) or getattr(ctx.state, "is_waiting", False):
        return
    req, session = ctx.req, ctx.session
    prefs = await effective_settings(
        req.db_mgr, session.tenant_org_id, "user_preferences", session.tenant_user_id
    )
    if not prefs.get("suggest_next_questions", True):
        return
    router = ctx.state.router
    model = router.model_for("suggest") if router is not None else ctx.effective_model
    suggestion = await suggest_followup(
        req.orchestrator.gateway, model, req.prompt, answer, ctx.state.executed_tools
    )
    if not suggestion:
        return
    # Persist the suggestion in the session record so it survives page reloads.

    def keep_suggestion(record: SessionRecord) -> None:
        if not isinstance(record.metadata, dict):
            record.metadata = {}
        record.metadata["suggested_followup"] = suggestion

    await req.store.mutate_session(ctx.session_id, session.tenant_org_id, keep_suggestion)
    await req.event_bus.publish(
        SessionEvent(
            session_id=ctx.session_id,
            event_type=EventType.FOLLOWUP_SUGGESTION,
            payload={"suggestion": suggestion},
        )
    )


async def persist_trace(state: TurnState, db_mgr: Any) -> None:
    """Exactly one trace per turn, whatever the outcome.

    The turn's transcript lives in the session history, but its tool calls, touched files and
    gates would otherwise be lost with the socket stream.
    """
    trace = state.trace
    if trace is None:
        return
    reasoning = "".join(state.reasoning_parts)
    if state.open_decision is not None and is_shutting_down():
        # Cancelled by a restart while waiting on a person: this turn did not complete. It
        # stays interrupted with its question, which the cockpit shows again.
        finished = trace.finish(
            status=TURN_STATUS_INTERRUPTED, error=INTERRUPTED_TURN_ERROR, reasoning=reasoning
        )
    else:
        finished = trace.finish(
            status=TURN_STATUS_ERROR if state.turn_error else TURN_STATUS_COMPLETED,
            error=str(state.turn_error) if state.turn_error else None,
            reasoning=reasoning,
        )
    await persist_turn_trace(db_mgr, finished)


async def housekeeping(req: TurnRequest, state: TurnState) -> None:
    """Turn metrics, then (for a turn that did not fail) memory distillation and AI titling."""
    # TTFT / turn-class histogram: a turn that ran any tool is sandbox-class;
    # a pure-answer turn is conversational.
    record_turn(
        "sandbox" if state.executed_tools else "conversational",
        max(0.0, time.monotonic() - state.started_at),
        (state.first_token_at - state.started_at) if state.first_token_at is not None else None,
    )
    if state.turn_error or getattr(state, "is_continuation", False):
        return
    session = await req.store.get_session(req.session_id)
    if not session:
        return
    router, fallback = state.router, state.effective_model or req.orchestrator.default_model
    if req.db_mgr is not None:
        # Distillation is auxiliary work, so route it to the "summarize" model (a fast/cheap
        # model under balanced/economy modes).
        await write_session_summary(
            memory_service=MemoryService(req.db_mgr),
            gateway=req.orchestrator.gateway,
            model=router.model_for("summarize") if router is not None else fallback,
            session_id=req.session_id,
            org_id=session.tenant_org_id,
            conversation=session.conversation_history,
            turn_index=user_turn_index(session.conversation_history),
        )
    # AI titling: one cheap structured call on the first turn, only while
    # the title is still a placeholder / heuristic value, and never over a user rename.
    # Best-effort: it cannot fail the turn.
    await apply_ai_title(
        store=req.store,
        event_bus=req.event_bus,
        gateway=req.orchestrator.gateway,
        model=router.model_for("suggest") if router is not None else fallback,
        session=session,
    )
