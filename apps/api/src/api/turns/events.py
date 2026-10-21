"""Turning the orchestrator's events into published session events, one small handler each."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from api.services.metrics import record_tool_call
from api.services.queued_messages import QueuedMessageStore
from api.services.session_support import _new_message_id
from api.services.turn_trace import record_running_tool_call
from api.turns.attention import update_attention
from api.turns.context import TurnContext
from api.turns.gates import _forget_pending_decisions, _remember_pending_decision
from specifications.interfaces.agent import AgentEvent
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.llm import ChatMessage
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


# Tools whose success means commits left the sandbox: each publication is audited on its own.
_PUBLISHING_TOOLS = frozenset({"git_push", "git_open_pr"})


def _published(result: object) -> bool:
    text = str(result or "")
    return text.startswith(("Published ", "Pushed ", "Pull request opened"))


class TokenCoalescer:
    """Buffers streaming token deltas to reduce event volume and bus write amplification.

    Flushes to the event bus when:
    1. Buffered character count reaches ``max_chars`` (~400 chars).
    2. Elapsed time since first buffered token reaches ``max_delay_ms`` (~100 ms).
    3. The token stream shifts between assistant text and reasoning thoughts.
    4. Any non-token event arrives (preserving causal ordering).
    5. The turn ends.
    """

    def __init__(
        self,
        publish_fn: Callable[[EventType, dict[str, Any]], Awaitable[None]],
        max_chars: int = 400,
        max_delay_ms: float = 100.0,
    ) -> None:
        self._publish_fn = publish_fn
        self._max_chars = max_chars
        self._max_delay_seconds = max_delay_ms / 1000.0
        self._current_type: EventType | None = None
        self._buffer: list[str] = []
        self._first_buffered_at: float = 0.0
        self._char_count: int = 0
        self._timer_task: asyncio.Task[None] | None = None
        # The timer flushes from its own task while the turn keeps adding: one flush at a time,
        # so chunks are published in the order they were streamed.
        self._flushing = asyncio.Lock()

    async def add(self, event_type: EventType, delta: str) -> None:
        if not delta:
            return
        now = time.monotonic()
        if self._current_type is not None and self._current_type != event_type:
            await self.flush()
        if self._current_type is None:
            self._current_type = event_type
            self._first_buffered_at = now
            self._schedule_timer()
        self._buffer.append(delta)
        self._char_count += len(delta)
        if (
            self._char_count >= self._max_chars
            or (now - self._first_buffered_at) >= self._max_delay_seconds
        ):
            await self.flush()

    def _schedule_timer(self) -> None:
        self._cancel_timer()
        self._timer_task = asyncio.create_task(self._timer_flush())

    def _cancel_timer(self) -> None:
        # Never the task doing the flush: the timer cancelling itself aborted the publish after
        # the buffer was taken, and the chunk was lost (thoughts streamed with words missing).
        if self._timer_task is not None and self._timer_task is not asyncio.current_task():
            self._timer_task.cancel()
        self._timer_task = None

    async def _timer_flush(self) -> None:
        try:
            await asyncio.sleep(self._max_delay_seconds)
        except asyncio.CancelledError:
            return
        await self.flush()

    async def flush(self) -> None:
        self._cancel_timer()
        async with self._flushing:
            if self._current_type is None or not self._buffer:
                self._current_type = None
                self._buffer.clear()
                self._char_count = 0
                return
            merged_delta = "".join(self._buffer)
            event_type = self._current_type
            self._current_type = None
            self._buffer.clear()
            self._char_count = 0
            await self._publish_fn(event_type, {"delta": merged_delta})


class TurnEventHandlers:
    """Dispatch table: one method per orchestrator event type."""

    def __init__(self, ctx: TurnContext) -> None:
        self.ctx = ctx
        self._coalescer = TokenCoalescer(self._publish)
        self._question_timers: set[asyncio.Task[None]] = set()
        self._intervening_tool_called = False
        self._step_text_parts: list[str] = []
        self._tool_prefaces: list[str] = []
        # The reasoning that led to a call, by call id, until the call completes.
        self._step_reasoning: dict[str, str] = {}
        # Calls whose output already reached the terminal while they ran.
        self._streamed_calls: set[str] = set()
        # What the session is marked as waiting on (see ``turns/attention.py``).
        self._awaiting: str | None = None
        self._handlers: dict[str, Handler] = {
            "model_usage": self._model_usage,
            "thought": self._thought,
            "tool_call": self._tool_call,
            "tool_completed": self._tool_completed,
            "file_diff": self._file_diff,
            "command_output": self._command_output,
            "interactive_question": self._interactive_question,
            "approval_required": self._approval_required,
            "plan_updated": self._plan_updated,
            "status": self._status,
            "subagent_started": self._forward(EventType.SUBAGENT_STARTED),
            "subagent_progress": self._forward(EventType.SUBAGENT_PROGRESS),
            "subagent_completed": self._forward(EventType.SUBAGENT_COMPLETED),
            "context_compacted": self._context_compacted,
            "post_failure_synthesis": self._post_failure_synthesis,
            "user_steer": self._user_steer,
            "steering_untaken": self._steering_untaken,
        }

    async def handle(self, event: AgentEvent) -> None:
        state = self.ctx.state
        if event.event_type != "interactive_question" and not (
            event.event_type == "status" and event.payload.get("state") == "paused_for_input"
        ):
            state.open_decision = None  # the turn moved on, so the question was answered
            if self._awaiting and event.event_type != "approval_required":
                await self._mark_awaiting(None)
        if event.event_type != "thought":
            await self._coalescer.flush()
        handler = self._handlers.get(event.event_type)
        if handler is not None:
            await handler(event)

    async def _mark_awaiting(self, kind: str | None) -> None:
        """Tell the sidebar the session waits on a person (or no longer does)."""
        if self._awaiting == kind:
            return
        self._awaiting = kind
        ctx = self.ctx
        await update_attention(
            ctx.req.store,
            ctx.req.event_bus,
            ctx.session_id,
            ctx.session.tenant_org_id,
            awaiting_input=kind,
            clear_awaiting=kind is None,
        )

    def _take_step_reasoning(self, call_id: str) -> None:
        """Keep the reasoning since the last call with this one (the first call of its step).

        Saved with the call, a reloaded session shows each thought between the steps it led
        to, as they streamed, instead of one block before every call of the turn.
        """
        state = self.ctx.state
        reasoning = "".join(state.reasoning_parts[state.reasoning_attached :]).strip()
        state.reasoning_attached = len(state.reasoning_parts)
        if reasoning and call_id:
            self._step_reasoning[call_id] = reasoning

    def _commit_step_text(self) -> None:
        if self._step_text_parts:
            text = "".join(self._step_text_parts)
            if text.strip():
                state = self.ctx.state
                if state.assistant_parts:
                    last = state.assistant_parts[-1]
                    separator = "\n" if last.endswith("\n") else "\n\n"
                    state.assistant_parts.append(separator)
                state.assistant_parts.append(text)
            self._step_text_parts.clear()

    async def flush(self) -> None:
        await self._coalescer.flush()
        self._commit_step_text()

    def close(self) -> None:
        """The turn is over: a question it asked can no longer time out."""
        for timer in self._question_timers:
            timer.cancel()

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

    async def _context_compacted(self, event: AgentEvent) -> None:
        self.ctx.trace.record_compaction(event.payload)
        await self._publish(EventType.CONTEXT_COMPACTED, event.payload)

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
        delta = str(event.payload.get("delta") or "")
        is_reasoning = bool(event.payload.get("is_reasoning", False))
        if not is_reasoning:
            if state.first_token_at is None and delta:
                state.first_token_at = time.monotonic()
            self._step_text_parts.append(delta)
        else:
            state.reasoning_parts.append(delta)
        event_type = EventType.REASONING_TOKEN if is_reasoning else EventType.TOKEN
        await self._coalescer.add(event_type, delta)
        await asyncio.sleep(0)

    async def _tool_call(self, event: AgentEvent) -> None:
        self._intervening_tool_called = True
        if self._step_text_parts:
            self._tool_prefaces.append("".join(self._step_text_parts))
            self._step_text_parts.clear()
        raw_args = event.payload.get("arguments", "{}")
        ctx = self.ctx
        if isinstance(raw_args, str) and ctx.redactor:
            redaction = ctx.redactor.redact(raw_args)
            if redaction is not None:
                raw_args = redaction.text
        arguments = _parse_arguments(raw_args)
        tool_name = event.payload.get("name", "")
        now_epoch = int(time.time())
        self._take_step_reasoning(str(event.payload.get("id") or ""))
        await self._publish(
            EventType.TOOL_STARTED,
            {
                "id": event.payload.get("id"),
                "name": tool_name,
                "arguments": arguments,
                "created_at": now_epoch,
            },
        )
        # Stream status updates for telemetry HUD
        if tool_name == "bash_exec":
            command = arguments.get("command", "")
            await self._publish(
                EventType.LOG_CHUNK,
                {
                    "text": f"\r\n\x1b[38;5;208m$ {command}\x1b[0m\r\n",
                    "tool_call_id": str(event.payload.get("id") or ""),
                },
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
            "created_at": int(time.time()),
        }
        total_redacted = (redaction.count if redaction is not None else 0) + int(
            event.payload.get("redacted") or 0
        )
        if total_redacted:
            # The "redaction applied" marker lives on the stored trace/event so a reader knows
            # the result was scrubbed, not that the tool returned this.
            tool_data["redacted"] = total_redacted
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
            redacted=bool(total_redacted > 0),
            redaction_count=total_redacted,
        )
        if tool_name in _PUBLISHING_TOOLS and _published(result_output):
            await ctx.audit.record_git_publish(
                org_id=ctx.session.tenant_org_id,
                actor_user_id=ctx.session.tenant_user_id,
                session_id=ctx.session_id,
                tool_name=tool_name,
                outcome=str(result_output).splitlines()[0],
            )
        await self._publish(EventType.TOOL_COMPLETED, tool_data)
        if reasoning := self._step_reasoning.pop(str(tool_data.get("id") or ""), ""):
            tool_data["reasoning"] = reasoning
        tool_data["seq"] = ctx.req.event_bus.latest_seq(ctx.session_id)
        ctx.state.executed_tools.append(tool_data)
        ctx.trace.record_tool_call(tool_data)
        await record_running_tool_call(
            ctx.req.db_mgr, ctx.trace.trace_id, ctx.session.tenant_org_id, tool_data
        )
        # A call that streamed its output has already shown it; the result only closes the block.
        call_id = str(tool_data.get("id") or "")
        if call_id in self._streamed_calls:
            self._streamed_calls.discard(call_id)
            await self._publish(EventType.LOG_CHUNK, {"text": "\r\n", "tool_call_id": call_id})
        elif tool_name == "bash_exec" and result_output:
            text = result_output if isinstance(result_output, str) else str(result_output)
            await self._publish(
                EventType.LOG_CHUNK, {"text": f"{text}\r\n", "tool_call_id": call_id}
            )

    async def _command_output(self, event: AgentEvent) -> None:
        """Output a running command printed so far (already redacted by the dispatcher)."""
        call_id = str(event.payload.get("tool_call_id") or "")
        self._streamed_calls.add(call_id)
        await self._publish(
            EventType.LOG_CHUNK,
            {"text": str(event.payload.get("text") or ""), "tool_call_id": call_id},
        )

    async def _file_diff(self, event: AgentEvent) -> None:
        self.ctx.trace.record_file_diff(event.payload)
        await self._publish(EventType.FILE_DIFF, event.payload)

    async def _interactive_question(self, event: AgentEvent) -> None:
        ctx = self.ctx
        ctx.state.open_decision = dict(event.payload)
        if ctx.req.db_mgr is not None:
            await _remember_pending_decision(ctx.req.db_mgr, ctx.trace, ctx.state.open_decision)
        await self._publish(EventType.DECISION_REQUIRED, event.payload)
        await self._mark_awaiting("question")
        timeout = ctx.governance.question_timeout_seconds
        if timeout and event.payload.get("question_id"):
            timer = asyncio.create_task(
                self._close_unanswered_question(event.payload, after_seconds=timeout)
            )
            self._question_timers.add(timer)
            timer.add_done_callback(self._question_timers.discard)

    async def _close_unanswered_question(
        self, question: dict[str, Any], *, after_seconds: int
    ) -> None:
        """Answer a question nobody answered in time, along the path a person's answer takes,
        so the turn resumes and every client closes the card."""
        await asyncio.sleep(after_seconds)
        ctx = self.ctx
        question_id = str(question["question_id"])
        recommended = question.get("default_recommended_option")
        fallback = (
            f"the recommended option ({recommended})" if recommended else "your best judgement"
        )
        note = (
            f"Nobody answered within {after_seconds} seconds. Do not ask again: continue with "
            f"{fallback} and state that assumption in your answer."
        )
        try:
            await ctx.req.orchestrator.submit_question_answer(
                session_id=ctx.session_id,
                question_id=question_id,
                selected_options=[],
                custom_text=note,
            )
        except KeyError:
            return  # answered in the meantime
        logger.info(
            "Question %s of %s closed unanswered after %ss",
            question_id,
            ctx.session_id,
            after_seconds,
        )
        await self._publish(
            EventType.DECISION_SUBMITTED,
            {"question_id": question_id, "selected_options": [], "custom_text": note},
        )
        await _forget_pending_decisions(ctx.req.db_mgr, ctx.session_id, ctx.session.tenant_org_id)

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
        else:
            await self._mark_awaiting("approval")

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
        if event.payload.get("discard_partial"):
            self.ctx.state.assistant_parts.clear()
            self._step_text_parts.clear()
            self.ctx.state.first_token_at = None
        if event.payload.get("status") in ("completed", "waiting"):
            self._commit_step_text()
        if event.payload.get("status") == "error":
            self.ctx.state.orchestrator_error = str(event.payload.get("error") or "The turn failed")
        if event.payload.get("status") == "waiting":
            self.ctx.state.is_waiting = True
        await self._publish(EventType.STATUS_CHANGED, event.payload)

    async def _user_steer(self, event: AgentEvent) -> None:
        """A message the person sent during the turn reached the model at this step.

        The session history keeps the order the model saw: the turn so far is saved as one
        assistant entry, then the message, and the rest of the turn becomes the next entry.
        """
        ctx, state = self.ctx, self.ctx.state
        self._commit_step_text()
        content = str(event.payload.get("content") or "")
        author = str(event.payload.get("author_user_id") or ctx.session.tenant_user_id)
        so_far = ChatMessage(
            role="assistant",
            content="".join(state.assistant_parts).strip(),
            reasoning="".join(state.reasoning_parts[state.reasoning_attached :]).strip() or None,
            tool_calls=list(state.executed_tools) or None,
        )
        message = ChatMessage(role="user", content=content, author_user_id=author)

        def split_turn(record: SessionRecord) -> None:
            if so_far.content or so_far.reasoning or so_far.tool_calls:
                record.conversation_history.append(so_far)
            record.conversation_history.append(message)

        await ctx.req.store.mutate_session(ctx.session_id, ctx.session.tenant_org_id, split_turn)
        if message_id := str(event.payload.get("message_id") or ""):
            # Taken by this turn: the end of the turn must not run it again.
            await QueuedMessageStore(ctx.req.db_mgr).mark_taken(
                ctx.session.tenant_org_id, ctx.session_id, message_id
            )
        state.earlier_reasoning.extend(state.reasoning_parts)
        state.reasoning_parts.clear()
        state.reasoning_attached = 0
        state.assistant_parts.clear()
        # In place: the running-turn snapshot a reconnecting tab gets shares this list, and the
        # steps before the message are now in the saved history.
        state.executed_tools.clear()
        await self._publish(
            EventType.USER_MESSAGE,
            {
                "message_id": str(event.payload.get("message_id") or _new_message_id()),
                "role": "user",
                "content": content,
                "author_user_id": author,
            },
        )

    async def _steering_untaken(self, event: AgentEvent) -> None:
        """Messages that arrived as the turn ended: the runner starts them as the next turns
        when this one has finished (``_start_queued_turns``), from the store. One handed to the
        orchestrator without going through the store is recorded there now."""
        ctx = self.ctx
        org = ctx.session.tenant_org_id
        store = QueuedMessageStore(ctx.req.db_mgr)
        known = {m.id for m in await store.pending(org, ctx.session_id)}
        for message in event.payload.get("messages") or []:
            message_id = str(message.get("message_id") or "") or f"msg_{uuid.uuid4().hex[:16]}"
            if message_id in known:
                continue
            await store.add(
                org,
                ctx.session_id,
                message_id,
                str(message.get("content") or ""),
                "steer",
                message.get("author_user_id"),
            )

    async def _post_failure_synthesis(self, event: AgentEvent) -> None:
        state = self.ctx.state
        state.had_post_failure_synthesis = True
        state.synthesis_turns = (
            event.payload.get("turns_taken")
            or event.payload.get("max_turns")
            or self.ctx.effective_max_turns
        )
        await self._publish(EventType.POST_FAILURE_SYNTHESIS, event.payload)
