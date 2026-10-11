"""Turning the orchestrator's events into published session events, one small handler each."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from api.services.metrics import record_tool_call
from api.turns.context import TurnContext
from api.turns.gates import _remember_pending_decision
from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.session import SessionRecord

logger = logging.getLogger(__name__)

Handler = Callable[[AgentEvent], Awaitable[None]]


def _parse_arguments(raw: Any) -> Any:
    """Tool arguments arrive as a JSON string; a malformed one becomes an empty object."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


class TurnEventHandlers:
    """Dispatch table: one method per orchestrator event type."""

    def __init__(self, ctx: TurnContext) -> None:
        self.ctx = ctx
        self._handlers: dict[str, Handler] = {
            "model_usage": self._model_usage,
            "thought": self._thought,
            "tool_call": self._tool_call,
            "tool_completed": self._tool_completed,
            "file_diff": self._file_diff,
            "interactive_question": self._interactive_question,
            "approval_required": self._approval_required,
            "plan_updated": self._plan_updated,
            "status": self._status,
            "subagent_started": self._forward(EventType.SUBAGENT_STARTED),
            "subagent_progress": self._forward(EventType.SUBAGENT_PROGRESS),
            "subagent_completed": self._forward(EventType.SUBAGENT_COMPLETED),
            "post_failure_synthesis": self._post_failure_synthesis,
        }

    async def handle(self, event: AgentEvent) -> None:
        state = self.ctx.state
        if event.event_type != "interactive_question" and not (
            event.event_type == "status" and event.payload.get("state") == "paused_for_input"
        ):
            state.open_decision = None  # the turn moved on, so the question was answered
        handler = self._handlers.get(event.event_type)
        if handler is not None:
            await handler(event)

    # -- helpers ------------------------------------------------------------------------

    async def _publish(self, event_type: EventType, payload: dict[str, Any]) -> None:
        await self.ctx.req.event_bus.publish(
            SessionEvent(session_id=self.ctx.session_id, event_type=event_type, payload=payload)
        )

    def _forward(self, event_type: EventType) -> Handler:
        async def forward(event: AgentEvent) -> None:
            await self._publish(event_type, event.payload)

        return forward

    # -- handlers -----------------------------------------------------------------------

    async def _model_usage(self, event: AgentEvent) -> None:
        # Per-turn token telemetry for cost attribution. Internal only: it is
        # recorded on the trace, never republished to the event bus.
        ctx = self.ctx
        ctx.trace.record_model_usage(event.payload)
        await ctx.audit.record_model_call(
            org_id=ctx.session.tenant_org_id,
            actor_user_id=ctx.session.tenant_user_id,
            session_id=ctx.session_id,
            model=str(event.payload.get("model") or ctx.effective_model or "unknown"),
            prompt_tokens=int(event.payload.get("prompt_tokens") or 0),
            completion_tokens=int(event.payload.get("completion_tokens") or 0),
        )

    async def _thought(self, event: AgentEvent) -> None:
        state = self.ctx.state
        delta = event.payload.get("delta", "")
        is_reasoning = event.payload.get("is_reasoning", False)
        if not is_reasoning:
            if state.first_token_at is None and delta:
                state.first_token_at = time.monotonic()
            state.assistant_parts.append(delta)
        else:
            state.reasoning_parts.append(delta)
        # Differentiate reasoning thoughts from assistant response text
        await self._publish(
            EventType.REASONING_TOKEN if is_reasoning else EventType.TOKEN, {"delta": delta}
        )
        await asyncio.sleep(0)

    async def _tool_call(self, event: AgentEvent) -> None:
        arguments = _parse_arguments(event.payload.get("arguments", "{}"))
        tool_name = event.payload.get("name", "")
        await self._publish(
            EventType.TOOL_STARTED,
            {"id": event.payload.get("id"), "name": tool_name, "arguments": arguments},
        )
        # Stream status updates for telemetry HUD
        if tool_name == "bash_exec":
            command = arguments.get("command", "")
            await self._publish(
                EventType.LOG_CHUNK, {"text": f"\r\n\x1b[38;5;208m$ {command}\x1b[0m\r\n"}
            )

    async def _tool_completed(self, event: AgentEvent) -> None:
        ctx = self.ctx
        arguments = _parse_arguments(event.payload.get("arguments", "{}"))
        tool_name = event.payload.get("name", "")
        result_output = event.payload.get("result", "")
        # Redaction: scrub tool output before it lands in the trace, the
        # persisted history or the terminal log. Only the marker and the count survive,
        # never the value that was removed.
        redaction = ctx.redactor.redact(result_output) if isinstance(result_output, str) else None
        if redaction is not None:
            result_output = redaction.text
        tool_data: dict[str, Any] = {
            "id": event.payload.get("id"),
            "name": tool_name,
            "arguments": arguments,
            "result": result_output,
            "status": event.payload.get("status", "completed"),
            "duration_ms": event.payload.get("duration_ms", 0),
        }
        if redaction is not None and redaction.applied:
            # The "redaction applied" marker lives on the stored trace/event so a reader knows
            # the result was scrubbed, not that the tool returned this.
            tool_data["redacted"] = redaction.count
        ctx.state.executed_tools.append(tool_data)
        ctx.trace.record_tool_call(tool_data)
        # Tool metric recorded at the app layer, not in the registry, so the agent-core
        # package never imports the application (layering invariant).
        record_tool_call(tool_name, tool_data.get("duration_ms", 0) / 1000.0 or None)
        await ctx.audit.record_tool_call(
            org_id=ctx.session.tenant_org_id,
            actor_user_id=ctx.session.tenant_user_id,
            session_id=ctx.session_id,
            tool_name=tool_name,
            status=str(tool_data.get("status") or "completed"),
            duration_ms=int(tool_data.get("duration_ms") or 0),
            redacted=bool(redaction is not None and redaction.applied),
            redaction_count=redaction.count if redaction is not None else 0,
        )
        await self._publish(EventType.TOOL_COMPLETED, tool_data)
        # Stream command output to the terminal stream for bash tools
        if tool_name == "bash_exec" and result_output:
            text = result_output if isinstance(result_output, str) else str(result_output)
            await self._publish(EventType.LOG_CHUNK, {"text": f"{text}\r\n"})

    async def _file_diff(self, event: AgentEvent) -> None:
        self.ctx.trace.record_file_diff(event.payload)
        await self._publish(EventType.FILE_DIFF, event.payload)

    async def _interactive_question(self, event: AgentEvent) -> None:
        ctx = self.ctx
        ctx.state.open_decision = dict(event.payload)
        if ctx.req.db_mgr is not None:
            await _remember_pending_decision(ctx.req.db_mgr, ctx.trace, ctx.state.open_decision)
        await self._publish(EventType.DECISION_REQUIRED, event.payload)

    async def _approval_required(self, event: AgentEvent) -> None:
        ctx = self.ctx
        ctx.trace.record_approval_request(event.payload)
        # Per-org approval policy. The default keeps the human gate; an
        # org can auto-approve or deny named risk levels, and the decision is enforced here on
        # the server, never by prompting the model.
        risk_level = event.payload.get("risk_level")
        action_type = event.payload.get("action_type")
        decision = ctx.governance.approval.decision_for(risk_level)
        if decision == "require_human" and ctx.binding.policy.permission_mode == "bypass":
            # Bypass mode: nothing waits for a person, but the organization's deny list above
            # still wins.
            decision = "auto_approve"
        await ctx.audit.record_approval_request(
            org_id=ctx.session.tenant_org_id,
            actor_user_id=ctx.session.tenant_user_id,
            session_id=ctx.session_id,
            action_type=action_type,
            risk_level=risk_level,
            decision=decision,
        )
        # `policy_decision` tells transports whether a human must answer or the
        # organization's policy already did.
        await self._publish(
            EventType.APPROVAL_REQUIRED, {**event.payload, "policy_decision": decision}
        )
        if decision in ("auto_approve", "deny"):
            await self._answer_by_policy(event, decision, risk_level, action_type)

    async def _answer_by_policy(
        self, event: AgentEvent, decision: str, risk_level: Any, action_type: Any
    ) -> None:
        ctx = self.ctx
        action_id = str(event.payload.get("action_id") or "")
        approved = decision == "auto_approve"
        if action_id:
            await ctx.req.orchestrator.submit_human_approval(
                session_id=ctx.session_id, action_id=action_id, approved=approved
            )
        await ctx.audit.record_approval_decision(
            org_id=ctx.session.tenant_org_id,
            actor_user_id=ctx.session.tenant_user_id,
            session_id=ctx.session_id,
            action_type=action_type,
            risk_level=risk_level,
            approved=approved,
            approved_by=f"policy:{decision}",
        )

    async def _plan_updated(self, event: AgentEvent) -> None:
        tasks = event.payload.get("tasks", [])

        def keep_checklist(record: SessionRecord) -> None:
            record.checklist = tasks
            if isinstance(record.metadata, dict):
                record.metadata["checklist"] = tasks

        await self.ctx.req.store.mutate_session(
            self.ctx.session_id, self.ctx.session.tenant_org_id, keep_checklist
        )
        await self._publish(EventType.PLAN_UPDATED, event.payload)

    async def _status(self, event: AgentEvent) -> None:
        if event.payload.get("status") == "error":
            self.ctx.state.orchestrator_error = str(event.payload.get("error") or "The turn failed")
        await self._publish(EventType.STATUS_CHANGED, event.payload)

    async def _post_failure_synthesis(self, event: AgentEvent) -> None:
        state = self.ctx.state
        state.had_post_failure_synthesis = True
        state.synthesis_turns = (
            event.payload.get("turns_taken")
            or event.payload.get("max_turns")
            or self.ctx.effective_max_turns
        )
        await self._publish(EventType.POST_FAILURE_SYNTHESIS, event.payload)
