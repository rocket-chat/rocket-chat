"""Async ReAct Orchestrator implementing AgentOrchestratorProtocol with modular subsystems."""

import asyncio
import dataclasses
import itertools
import json
import logging
import os
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Any

from llm_gateway.pricing import estimate_cost_usd
from llm_gateway.reasoning import merge_reasoning_blocks
from llm_gateway.tokens import estimated_usage, request_tokens

from agent_core.checklist import stale_notice, unfinished_notice, verification_gate_notice
from agent_core.context import (
    ContextManager,
    ExecutionContext,
    Summarizer,
    _current_request,
    current_execution_ctx,
)
from agent_core.context_policy import assemble_request_messages, format_attachment_manifest
from agent_core.dispatcher import ToolDispatcher
from agent_core.guidance import (
    in_effect_at_turn_start,
    render_index,
    render_note,
    servers_of_tools,
)
from agent_core.interaction import DecisionGateManager
from agent_core.leaked_calls import (
    LEAK_GIVE_UP,
    LEAK_NOTICE,
    MAX_LEAK_RETRIES,
    LeakFilter,
    contains_leaked_calls,
    strip_leaked_calls,
)
from agent_core.notes import automatic_turn_prompt, without_engine_notes
from agent_core.pace import pace_notice
from agent_core.progress import LoopDetectionResult, ProgressTracker
from agent_core.reasoning_guard import (
    StepReasoningOverrunError,
    decisive_request,
    reasoning_tokens_estimate,
)
from agent_core.retry_policy import execute_stream_with_retry
from agent_core.standing import (
    changed_skills,
    changed_skills_note,
    render_skills,
    skills_at_turn_start,
    skills_missing_from,
)
from agent_core.subagents import SubagentRunner
from agent_core.summarizer import model_summarizer
from agent_core.tool_loading import ALWAYS_LOADED, ESSENTIAL_TOOLS, catalogue, deferred_names
from agent_core.tool_slot import human_wait
from agent_core.tools.registry import ToolRegistry
from agent_core.transcript import RecordingList, repair_tool_pairs
from agent_core.truncation import (
    MAX_EMPTY_RETRIES,
    empty_reply_give_up,
    empty_reply_notice,
    split_truncated,
    truncation_notice,
)
from specifications.interfaces.agent import (
    APPROVAL_GRANTED_PREFIX,
    DEFAULT_MAX_TURNS,
    NO_STEP_LIMIT,
    AgentEvent,
    AgentOrchestratorProtocol,
    ApprovalRequest,
    ClusterRelayProtocol,
    FileDiff,
    GenerationParams,
    InteractiveQuestion,
    SteeringMessage,
    SubagentConfig,
    SubagentTaskRequest,
    SystemPrompt,
    TurnCallbacks,
    TurnPolicy,
)
from specifications.interfaces.compaction import CompactionCheckpoint, CompactionStoreProtocol
from specifications.interfaces.llm import (
    CONTEXT_WINDOW_EXCEEDED,
    AttachmentRef,
    ChatMessage,
    ContextWindowExceededError,
    LLMGatewayProtocol,
    ModelCatalogueProtocol,
    ModelExecutionError,
    ModelRequest,
    ModelRouterProtocol,
    ModelScope,
    StreamChunk,
    ToolDefinition,
    current_model_scope,
)
from specifications.interfaces.transcript import TranscriptStoreProtocol

logger = logging.getLogger(__name__)


DEFAULT_TEMPERATURE = 0.2


class OrchestratorState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PLANNING = "planning"
    PAUSED_FOR_INPUT = "paused_for_input"
    WAITING = "waiting"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class _ModelReply:
    """What one model call produced, reasoning included (some providers need it back)."""

    text: str
    tool_calls: dict[int, dict[str, Any]]
    reasoning: str = ""
    reasoning_blocks: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    cost_usd: float = 0.0


LOADING_TOOLS = ALWAYS_LOADED - {"load_guidance"}


class _StreamCollector:
    """Folds one model stream into a reply, forwarding reasoning and answer text as they come."""

    def __init__(
        self,
        session_id: str,
        event_queue: asyncio.Queue[AgentEvent | None],
        emit_answer: Callable[[str, asyncio.Queue[AgentEvent | None], str], Awaitable[None]],
        reasoning_limit_tokens: int = 0,
    ) -> None:
        self.reasoning_limit_tokens = reasoning_limit_tokens
        self.session_id = session_id
        self.event_queue = event_queue
        self.emit_answer = emit_answer
        self.leak_filter = LeakFilter()
        self.started_at = time.monotonic()
        self.text = ""
        self.reasoning = ""
        self.reasoning_blocks: list[dict[str, Any]] = []
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.reported_usage: dict[str, int] | None = None
        self.reported_cost: float | None = None
        self.finish_reason: str | None = None

    async def feed(self, chunk: StreamChunk) -> None:
        if chunk.error:
            if chunk.finish_reason == CONTEXT_WINDOW_EXCEEDED:
                raise ContextWindowExceededError(chunk.error)
            raise ModelExecutionError(chunk.error)
        if chunk.token_usage:
            self.reported_usage = chunk.token_usage
            self.reported_cost = chunk.cost_usd
        if chunk.finish_reason:
            self.finish_reason = chunk.finish_reason
        if chunk.reasoning_blocks:
            merge_reasoning_blocks(self.reasoning_blocks, chunk.reasoning_blocks)
        if chunk.reasoning_delta:
            self.reasoning += chunk.reasoning_delta
            await self.event_queue.put(
                AgentEvent(
                    event_type="thought",
                    session_id=self.session_id,
                    payload={"delta": chunk.reasoning_delta, "is_reasoning": True},
                )
            )
            await asyncio.sleep(0)
            self._check_reasoning_limit()
        if chunk.text_delta:
            self.text += chunk.text_delta
            await self.emit_answer(
                self.session_id, self.event_queue, self.leak_filter.feed(chunk.text_delta)
            )
            await asyncio.sleep(0)
        if chunk.tool_call_delta:
            self._add_tool_call_delta(chunk.tool_call_delta)

    def _check_reasoning_limit(self) -> None:
        acting = bool(self.text or self.tool_calls)
        if (
            self.reasoning_limit_tokens
            and not acting
            and reasoning_tokens_estimate(self.reasoning) > self.reasoning_limit_tokens
        ):
            raise StepReasoningOverrunError(self)

    def _add_tool_call_delta(self, delta: dict[str, Any]) -> None:
        index = delta.get("index", 0)
        call = self.tool_calls.setdefault(
            index, {"id": delta.get("id"), "name": "", "arguments": ""}
        )
        function = delta.get("function") or {}
        if function.get("name"):
            call["name"] = function["name"]
        if function.get("arguments"):
            call["arguments"] += function["arguments"]

    def produced_text(self) -> str:
        """Everything the model wrote, tool-call arguments included: all of it is billed."""
        arguments = "".join(call["arguments"] for call in self.tool_calls.values())
        return self.text + self.reasoning + arguments

    def reply(self, cost_usd: float = 0.0) -> _ModelReply:
        return _ModelReply(
            self.text,
            self.tool_calls,
            self.reasoning,
            self.reasoning_blocks,
            self.finish_reason,
            cost_usd=cost_usd,
        )


@dataclass
class _TurnRun:
    """Everything one ReAct turn needs between its phases."""

    session_id: str
    org_id: str
    conversation: list[ChatMessage]
    seed_messages: list[ChatMessage]
    react_model: str
    summary_model: str
    turn_limit: int
    event_queue: asyncio.Queue[AgentEvent | None]
    whitelisted_tools: list[str] | None
    exec_ctx: ExecutionContext | None
    pending_attachments: list[AttachmentRef]
    checklist_reminded: bool = False
    verification_reminded: bool = False
    active_checklist: list[dict[str, Any]] | None = None
    cumulative_cost_usd: float = 0.0
    step_costs: list[float] = field(default_factory=list)
    leak_retries: int = 0
    empty_retries: int = 0
    step: int = 0
    last_compaction_step: int = -999
    # The provider refused a request as larger than its window once; the next refusal is final.
    compacted_for_overflow: bool = False
    # Engine instructions for the model (see notes.py): rendered at the end of requests, never
    # stored. Sticky ones last for the rest of the turn, the others for the next request only.
    sticky_notes: list[str] = field(default_factory=list)
    next_notes: list[str] = field(default_factory=list)
    progress_tracker: ProgressTracker = field(default_factory=ProgressTracker)
    # The volatile context of this turn (routing, background processes, GitHub activity,

    # recalled memories): after the conversation, so it never breaks the cached prefix.
    context_note: str = ""
    synthetic_prompt: bool = False
    # ``.rocket/PLAN.md`` and ``PROGRESS.md`` as last read; ``None`` until read, or once a step
    # may have changed them.
    working_memory: tuple[str | None, str | None] | None = None

    def current_notes(self) -> list[str]:
        return [
            *([self.context_note] if self.context_note else []),
            *self.sticky_notes,
            *self.next_notes,
        ]

    def on_last_step(self, step: int) -> bool:
        return self.turn_limit != NO_STEP_LIMIT and step >= self.turn_limit - 1

    def take_notes(self) -> list[str]:
        notes = [*([self.context_note] if self.context_note else []), *self.sticky_notes]
        notes, self.next_notes = [*notes, *self.next_notes], []
        return notes


_WORKING_MEMORY_FILES = ("PLAN.md", "PROGRESS.md")
_WORKING_MEMORY_NOTE_CHARS = 12_000


def _mentions_working_memory(call: dict[str, Any]) -> bool:
    """Whether a tool call may have written ``.rocket/PLAN.md`` or ``PROGRESS.md``."""
    arguments = str(call.get("arguments") or (call.get("function") or {}).get("arguments") or "")
    return any(name in arguments for name in _WORKING_MEMORY_FILES)


def _working_memory_note(plan_md: str | None, progress_md: str | None) -> str:
    """The working-memory files as they were when the turn started, for the turn's context.

    Given once with the turn's fixed context (a cached position) rather than at the end of every
    request: within the turn the agent sees its own edits to them.
    """
    sections = [
        f"{title}:\n{text.strip()[:_WORKING_MEMORY_NOTE_CHARS]}"
        for title, text in (
            ("Working Plan (.rocket/PLAN.md)", plan_md),
            ("Progress (.rocket/PROGRESS.md)", progress_md),
        )
        if text and text.strip()
    ]
    return "\n\n".join(sections)


def _steering_payload(message: SteeringMessage) -> dict[str, Any]:
    return {
        "content": message.content,
        "message_id": message.message_id,
        "author_user_id": message.author_user_id,
    }


class AsyncReActOrchestrator(AgentOrchestratorProtocol):
    """
    Autonomous ReAct execution engine managing reasoning, tool dispatch,
    and interactive human-in-the-loop decision gating.
    """

    PAUSED_FOR_INPUT = OrchestratorState.PAUSED_FOR_INPUT.value
    WAITING = OrchestratorState.WAITING.value
    RUNNING = OrchestratorState.RUNNING.value
    PLANNING = OrchestratorState.PLANNING.value
    IDLE = OrchestratorState.IDLE.value
    COMPLETED = OrchestratorState.COMPLETED.value
    CANCELLED = OrchestratorState.CANCELLED.value

    def __init__(
        self,
        gateway: LLMGatewayProtocol,
        registry: ToolRegistry,
        default_model: str = "openrouter/deepseek/deepseek-v4.1-flash",
        max_turns: int = DEFAULT_MAX_TURNS,
        subagent_configs: dict[str, SubagentConfig] | None = None,
        transcript_store: TranscriptStoreProtocol | None = None,
        compaction_store: CompactionStoreProtocol | None = None,
        catalogue: ModelCatalogueProtocol | None = None,
    ) -> None:
        self._gateway: LLMGatewayProtocol = gateway
        self._registry: ToolRegistry = registry
        self.default_model: str = default_model
        self.max_turns: int = max_turns
        self.fallback_models: list[str] = [
            m.strip() for m in (os.getenv("ROCKET_FALLBACK_MODELS") or "").split(",") if m.strip()
        ]

        # Decomposed modular subsystems
        self.dispatcher = ToolDispatcher(registry=registry)
        self.subagent_runner = SubagentRunner(
            gateway=gateway, registry=registry, configs=subagent_configs, dispatcher=self.dispatcher
        )
        self._registry.bind_subagent_runner(self.subagent_runner)
        self.gates = DecisionGateManager()
        # Set by multi-replica deployments so decisions can reach the replica running the turn.
        self.cluster_relay: ClusterRelayProtocol | None = None
        # Wraps every wait on a human decision so the host can release scarce resources
        # (the process-wide turn slot) while the turn is parked.
        self.human_wait_scope: Callable[[], AbstractAsyncContextManager[None]] | None = None
        self.context_manager = ContextManager(catalogue=catalogue)
        # Messages the person sent while their session's turn runs, taken at its next step.
        self._live_turns: set[str] = set()
        self._steering: dict[str, list[SteeringMessage]] = {}
        self._overhead_cache: dict[tuple[str, int, tuple[str, ...]], int] = {}

        # The durable log is the source of truth; this is a bounded cache of live
        # conversations, rehydrated from the store when a session is not resident.
        self.transcript_store: TranscriptStoreProtocol | None = transcript_store
        self.compaction_store: CompactionStoreProtocol | None = compaction_store
        self.max_cached_sessions: int = int(os.getenv("ROCKET_MAX_CACHED_SESSIONS", "256") or 256)
        self._session_histories: OrderedDict[str, list[ChatMessage]] = OrderedDict()
        self._session_states: dict[str, str] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._active_tasks: dict[str, asyncio.Task[None]] = {}

    async def _wait_for_human(self, wait_event: asyncio.Event) -> None:
        # A person may take hours: the waiting tool call keeps no concurrency permit and its
        # clock stops (human_wait), and the host may release the process-wide turn slot.
        async with human_wait():
            if self.human_wait_scope is None:
                await wait_event.wait()
                return
            async with self.human_wait_scope():
                await wait_event.wait()

    async def _conversation_for(self, session_id: str, org_id: str) -> list[ChatMessage]:
        """The live conversation for a session, rehydrated from the durable log if needed."""
        resident = self._session_histories.get(session_id)
        if resident is not None:
            self._session_histories.move_to_end(session_id)
            return resident

        loaded: list[ChatMessage] = []
        if self.transcript_store is not None:
            try:
                latest = None
                if self.compaction_store is not None:
                    latest = await self.compaction_store.get_latest_compaction(session_id, org_id)
                if latest is not None:
                    tail = await self.transcript_store.load(
                        session_id, org_id, since_seq=latest.boundary_seq
                    )
                    loaded = [*latest.compacted_messages, *tail]
                else:
                    loaded = await self.transcript_store.load(session_id, org_id)
            except Exception:
                # Starting cold is better than failing the turn; the failure is logged.
                logger.exception("Transcript rehydration failed for session %s", session_id)
        # Older versions stored control notices as mid-history system messages; they must not
        # reach a provider again (see notes.py).
        repaired = repair_tool_pairs(without_engine_notes(loaded))
        if self.transcript_store is not None:
            existing_ids = {m.tool_call_id for m in loaded if m.role == "tool"}
            added_repairs = [
                m for m in repaired if m.role == "tool" and m.tool_call_id not in existing_ids
            ]
            if added_repairs:
                try:
                    await self.transcript_store.append(session_id, org_id, added_repairs)
                except Exception:
                    logger.warning(
                        "Failed to persist repaired tool placeholders for %s", session_id
                    )
        conversation = RecordingList(repaired)
        self._session_histories[session_id] = conversation
        return conversation

    async def compact_history(
        self,
        session_id: str,
        org_id: str,
        model: str | None = None,
    ) -> Any:
        """Manually trigger compaction for a session's history."""
        conversation = await self._conversation_for(session_id, org_id)
        if len(conversation) < 3:
            return None
        effective_model = model or self.default_model
        # A manual compaction has no turn, so no tool set to share a cache with.
        summarize = self._make_summarizer(effective_model)
        cleaned = await self.context_manager.enforce_hygiene_async(
            effective_model,
            list(conversation),
            summarize=summarize,
            force_compaction=True,
        )
        if cleaned != conversation:
            del conversation[:]
            conversation.extend(cleaned)
            boundary_seq = 0
            if self.transcript_store is not None:
                latest_seq = await self.transcript_store.latest_seq(session_id, org_id)
                tail_count = getattr(cleaned, "tail_count", None)
                if tail_count is None:
                    tail_count = len(cleaned) - len(getattr(cleaned, "compacted_prefix", []))
                boundary_seq = max(0, (latest_seq or 0) - max(0, tail_count))

            if self.compaction_store is not None:
                try:
                    checkpoint = CompactionCheckpoint(
                        id=f"cmp_{uuid.uuid4().hex[:24]}",
                        session_id=session_id,
                        org_id=org_id,
                        boundary_seq=boundary_seq,
                        summary=getattr(cleaned, "summary", None),
                        summary_status=getattr(cleaned, "summary_status", "") or "legacy",
                        compacted_messages=getattr(cleaned, "compacted_prefix", []),
                        tokens_before=getattr(cleaned, "tokens_before", 0),
                        tokens_after=getattr(cleaned, "tokens_after", 0),
                        created_at=int(time.time()),
                    )
                    await self.compaction_store.record_compaction(checkpoint)
                except Exception:
                    logger.exception("Failed to record compaction checkpoint for %s", session_id)
            return cleaned
        return None

    async def _flush_transcript(
        self, session_id: str, org_id: str, conversation: list[ChatMessage]
    ) -> None:
        """Persist messages appended since the last flush; failures are retried next time."""
        if self.transcript_store is None or not isinstance(conversation, RecordingList):
            return
        pending = conversation.take_pending()
        if not pending:
            return
        try:
            await self.transcript_store.append(session_id, org_id, pending)
        except Exception:
            logger.exception("Transcript flush failed for session %s", session_id)
            conversation.restore_pending(pending)

    def _evict_idle_sessions(self) -> None:
        """Drop least-recently-used conversations beyond the cache bound.

        Only with a durable store (otherwise eviction would lose the conversation) and
        never a session with a running turn or unflushed messages.
        """
        if self.transcript_store is None:
            return
        for session_id in list(self._session_histories):
            if len(self._session_histories) <= self.max_cached_sessions:
                break
            conversation = self._session_histories[session_id]
            busy = session_id in self._active_tasks or (
                isinstance(conversation, RecordingList) and conversation.pending
            )
            if busy:
                continue
            del self._session_histories[session_id]
            self._session_states.pop(session_id, None)
            self._cancel_flags.pop(session_id, None)

    @property
    def gateway(self) -> LLMGatewayProtocol:
        """Expose LLM gateway instance for follow-up suggestions and direct model queries."""
        return self._gateway

    @property
    def registry(self) -> ToolRegistry:
        """Expose ToolRegistry instance for dynamic tools and external MCP binding."""
        return self._registry

    # Backward-compatibility properties for tests inspecting internal state
    @property
    def _file_locks(self) -> dict[tuple[str, str], list[Any]]:
        return self.dispatcher._file_locks

    @property
    def _pending_questions(self) -> dict[str, Any]:
        return self.gates._pending_questions

    @property
    def _question_answers(self) -> dict[str, Any]:
        return self.gates._question_answers

    @property
    def _pending_approvals(self) -> dict[str, Any]:
        return self.gates._pending_approvals

    @property
    def _approval_decisions(self) -> dict[str, Any]:
        return self.gates._approval_decisions

    def get_session_state(self, session_id: str) -> str:
        """Return the current execution state of an agent session."""
        return self._session_states.get(session_id, OrchestratorState.IDLE.value)

    def _build_turn_callbacks(
        self,
        session_id: str,
        event_queue: asyncio.Queue[AgentEvent | None],
        parent_model: str | None = None,
        model_router: ModelRouterProtocol | None = None,
        external: TurnCallbacks | None = None,
    ) -> TurnCallbacks:
        """Collaborators for one turn, closed over this turn's queue and session.

        They travel on the turn's ``ExecutionContext`` rather than being installed on the
        shared registry, so concurrent turns cannot answer each other's approvals.
        ``external`` carries the transport's own collaborators (sandbox provisioning,
        rename, knowledge search, skills).
        """

        def _on_diff(diff: FileDiff) -> None:
            event = AgentEvent(
                event_type="file_diff",
                session_id=session_id,
                payload=asdict(diff),
            )
            event_queue.put_nowait(event)

        async def _on_question(
            question: str,
            options: list[str],
            is_multi_select: bool,
            default_rec: str | None,
        ) -> str:
            question_id = f"q_{uuid.uuid4().hex[:8]}"
            q_obj = InteractiveQuestion(
                question_id=question_id,
                question_text=question,
                options=options,
                is_multi_select=is_multi_select,
                default_recommended_option=default_rec,
            )
            wait_event = asyncio.Event()
            self.gates.register_question(question_id, session_id, wait_event, q_obj)
            self._session_states[session_id] = OrchestratorState.PAUSED_FOR_INPUT.value

            await event_queue.put(
                AgentEvent(
                    event_type="interactive_question",
                    session_id=session_id,
                    payload=asdict(q_obj),
                )
            )
            await event_queue.put(
                AgentEvent(
                    event_type="status",
                    session_id=session_id,
                    payload={
                        "state": OrchestratorState.PAUSED_FOR_INPUT.value,
                        "question_id": question_id,
                    },
                )
            )

            try:
                await self._wait_for_human(wait_event)
            finally:
                answer = self.gates.forget_question(question_id)

            if self._cancel_flags.get(session_id, False):
                return "Execution was cancelled by the user."

            selected_options, custom_text = answer
            self._session_states[session_id] = OrchestratorState.RUNNING.value
            await event_queue.put(
                AgentEvent(
                    event_type="status",
                    session_id=session_id,
                    payload={"state": OrchestratorState.RUNNING.value},
                )
            )
            return f"User response: {', '.join(selected_options)}. Notes: {custom_text or 'None'}"

        def _on_plan(tasks: list[dict[str, Any]]) -> None:
            event = AgentEvent(
                event_type="plan_updated",
                session_id=session_id,
                payload={"session_id": session_id, "tasks": tasks},
            )
            event_queue.put_nowait(event)

        async def _on_subagent(role: str, task: str, context: str = "") -> str:
            sub_req = SubagentTaskRequest(role=role, task=task, context=context)
            if model_router is not None:
                sub_model = model_router.model_for(f"subagent:{role}")
            else:
                sub_model = parent_model or self.default_model
            sub_res = await self.subagent_runner.run(
                request=sub_req,
                session_id=session_id,
                event_queue=event_queue,
                default_model=sub_model,
            )
            return (
                f"[{sub_res.role.upper()} SUBAGENT REPORT]\n"
                f"Task: {sub_res.task}\n"
                f"Status: {sub_res.status} ({sub_res.turns_taken} turns, {sub_res.tool_calls_count} tools, {sub_res.duration_ms}ms)\n\n"
                f"{sub_res.summary}"
            )

        async def _on_approval(action_type: str, description: str, risk_level: str) -> str:
            """Pause the turn until a human approves or denies a sensitive action."""
            action_id = f"ap_{uuid.uuid4().hex[:8]}"
            approval = ApprovalRequest(
                action_id=action_id,
                action_type=action_type,
                command_or_path=description,
                risk_level=risk_level,
            )
            wait_event = asyncio.Event()
            self.gates.register_approval(action_id, session_id, wait_event, approval)
            self._session_states[session_id] = OrchestratorState.PAUSED_FOR_INPUT.value

            await event_queue.put(
                AgentEvent(
                    event_type="approval_required",
                    session_id=session_id,
                    payload=asdict(approval),
                )
            )
            await event_queue.put(
                AgentEvent(
                    event_type="status",
                    session_id=session_id,
                    payload={
                        "state": OrchestratorState.PAUSED_FOR_INPUT.value,
                        "action_id": action_id,
                    },
                )
            )

            try:
                await self._wait_for_human(wait_event)
            finally:
                approved = self.gates.forget_approval(action_id)

            if self._cancel_flags.get(session_id, False):
                return "Approval aborted: execution was cancelled by the user."

            self._session_states[session_id] = OrchestratorState.RUNNING.value
            await event_queue.put(
                AgentEvent(
                    event_type="status",
                    session_id=session_id,
                    payload={"state": OrchestratorState.RUNNING.value},
                )
            )
            if approved:
                return f"{APPROVAL_GRANTED_PREFIX}: proceed with '{action_type}'."
            return (
                f"Denied by the user: do NOT perform '{action_type}'. "
                "Acknowledge the decision and continue with a safe alternative."
            )

        return replace(
            external or TurnCallbacks(),
            diff=_on_diff,
            question=_on_question,
            plan=_on_plan,
            subagent=_on_subagent,
            approval=_on_approval,
        )

    def process_user_turn(
        self,
        session_id: str,
        user_message: str,
        model: str | None = None,
        system_prompt: str | None = None,
        system_prompt_parts: SystemPrompt | None = None,
        whitelisted_tools: list[str] | None = None,
        denied_tools: frozenset[str] | None = None,
        tenant_org_id: str = "default_org",
        tenant_user_id: str = "dev_user",
        max_turns: int | None = None,
        attachments: list[AttachmentRef] | None = None,
        model_router: ModelRouterProtocol | None = None,
        zero_retention: bool = False,
        region: str = "",
        enabled_toolsets: frozenset[str] | None = None,
        callbacks: TurnCallbacks | None = None,
        policy: TurnPolicy | None = None,
        synthetic_prompt: bool = False,
    ) -> AsyncIterator[AgentEvent]:
        event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        effective_model = model or self.default_model

        # Wire contextvars execution context for multi-session concurrency safety
        exec_ctx = ExecutionContext(
            session_id=session_id,
            event_queue=event_queue,
            parent_model=effective_model,
            tenant_org_id=tenant_org_id,
            tenant_user_id=tenant_user_id,
            allowed_tools=(
                frozenset(whitelisted_tools)
                if whitelisted_tools is not None and "*" not in whitelisted_tools
                else None
            ),
            denied_tools=denied_tools or frozenset(),
            zero_retention=zero_retention,
            region=region,
            enabled_toolsets=enabled_toolsets or frozenset(),
            plan_mode=(policy or TurnPolicy()).permission_mode == "plan",
            policy=policy or TurnPolicy(),
            sandbox_capabilities=(policy.sandbox_capabilities if policy is not None else None),
            active_repository=(policy.active_repository if policy is not None else ""),
            agent_env=dict(policy.agent_env) if policy is not None else {},
            callbacks=self._build_turn_callbacks(
                session_id,
                event_queue,
                parent_model=effective_model,
                model_router=model_router,
                external=callbacks,
            ),
        )
        current_execution_ctx.set(exec_ctx)
        # Every model call this turn makes (ReAct steps, subagents, and the title/summary/
        # follow-up calls the caller makes afterwards) is attributed to, and budget-checked
        # for, this tenant, member and session.
        current_model_scope.set(
            ModelScope(
                org_id=tenant_org_id,
                user_id=tenant_user_id,
                session_id=session_id,
                zero_retention=zero_retention,
                region=region,
                provider_preferences=exec_ctx.policy.generation.provider_preferences,
            )
        )

        self._cancel_flags[session_id] = False
        self._session_states[session_id] = OrchestratorState.RUNNING.value
        system_prompt, system_prompt_parts = self._defer_tools(
            exec_ctx, whitelisted_tools, system_prompt, system_prompt_parts
        )

        task = asyncio.create_task(
            self._run_react_cycle(
                session_id,
                user_message,
                event_queue,
                model=effective_model,
                system_prompt=system_prompt,
                system_prompt_parts=system_prompt_parts,
                whitelisted_tools=whitelisted_tools,
                exec_ctx=exec_ctx,
                max_turns=max_turns,
                attachments=attachments,
                model_router=model_router,
                synthetic_prompt=synthetic_prompt,
            )
        )
        self._active_tasks[session_id] = task

        async def _generator() -> AsyncIterator[AgentEvent]:
            try:
                while True:
                    event = await event_queue.get()
                    if event is None:
                        break
                    yield event
            finally:
                self._active_tasks.pop(session_id, None)

        return _generator()

    @staticmethod
    async def _emit_answer_text(
        session_id: str, event_queue: asyncio.Queue[AgentEvent | None], text: str
    ) -> None:
        """Stream a piece of the answer to the client (nothing for an empty or withheld piece)."""
        if text:
            await event_queue.put(
                AgentEvent(
                    event_type="thought",
                    session_id=session_id,
                    payload={"delta": text, "is_reasoning": False},
                )
            )

    async def _stream_model_step(
        self,
        session_id: str,
        messages: list[ChatMessage],
        event_queue: asyncio.Queue[AgentEvent | None],
        model: str | None = None,
        whitelisted_tools: list[str] | None = None,
        role: str = "react",
        tool_choice: str | None = None,
    ) -> _ModelReply:
        tools_for_request = self._tools_for_request(whitelisted_tools)

        exec_ctx = current_execution_ctx.get()
        generation = exec_ctx.policy.generation if exec_ctx else GenerationParams()
        request = ModelRequest(
            model=model or self.default_model,
            messages=messages,
            tools=tools_for_request,
            # Organization fail-over models first, then the deployment-wide ones.
            fallback_models=[*generation.fallback_models, *self.fallback_models],
            tool_choice=tool_choice,
            thinking_budget_tokens=generation.thinking_budget_tokens,
            reasoning_effort=generation.reasoning_effort,
            temperature=(
                generation.temperature
                if generation.temperature is not None
                else DEFAULT_TEMPERATURE
            ),
            max_tokens=generation.max_tokens,
            # Model data governance: every request this turn makes carries
            # the org's zero-retention/region requirement.
            zero_retention=exec_ctx.zero_retention if exec_ctx else False,
            region=exec_ctx.region if exec_ctx else "",
        )

        limit = generation.reasoning_step_limit_tokens if role == "react" else 0
        try:
            stream, eff_model = await self._execute_stream(request, session_id, event_queue, limit)
        except StepReasoningOverrunError as overrun:
            aborted = overrun.collector
            assert isinstance(aborted, _StreamCollector)
            await self._report_step_usage(
                aborted,
                request.model,
                messages,
                tools_for_request,
                role,
                exec_ctx,
                session_id,
                event_queue,
                finish_reason="reasoning_limit",
            )
            await self._announce_decision_retry(session_id, event_queue, limit)
            stream, eff_model = await self._execute_stream(
                decisive_request(request, openrouter=request.model.startswith("openrouter/")),
                session_id,
                event_queue,
                0,
            )
        await self._emit_answer_text(session_id, event_queue, stream.leak_filter.finish())
        step_cost = await self._report_step_usage(
            stream, eff_model, messages, tools_for_request, role, exec_ctx, session_id, event_queue
        )
        return stream.reply(cost_usd=step_cost)

    async def _execute_stream(
        self,
        request: ModelRequest,
        session_id: str,
        event_queue: asyncio.Queue[AgentEvent | None],
        reasoning_limit_tokens: int,
    ) -> tuple[_StreamCollector, str]:
        return await execute_stream_with_retry(
            gateway=self._gateway,
            request=request,
            collector_factory=lambda: _StreamCollector(
                session_id, event_queue, self._emit_answer_text, reasoning_limit_tokens
            ),
            feed_chunk=lambda collector, chunk: collector.feed(chunk),
            session_id=session_id,
            event_queue=event_queue,
        )

    @staticmethod
    async def _announce_decision_retry(
        session_id: str, event_queue: asyncio.Queue[AgentEvent | None], limit: int
    ) -> None:
        await event_queue.put(
            AgentEvent(
                event_type="thought",
                session_id=session_id,
                payload={
                    "delta": (
                        f"\n[Reasoned past {limit:,} tokens without acting: asking again with "
                        "reasoning off so the step decides.]\n"
                    ),
                    "is_reasoning": True,
                },
            )
        )

    async def _report_step_usage(
        self,
        stream: _StreamCollector,
        eff_model: str,
        messages: list[ChatMessage],
        tools_for_request: list[ToolDefinition],
        role: str,
        exec_ctx: ExecutionContext | None,
        session_id: str,
        event_queue: asyncio.Queue[AgentEvent | None],
        finish_reason: str | None = None,
    ) -> float:
        """Record one call's usage in the ledger and publish its ``model_usage`` event."""
        reported_usage = stream.reported_usage

        # Record usage telemetry into gateway ledger for org and user accounting
        cur_ctx = current_execution_ctx.get()
        t_org = cur_ctx.tenant_org_id if cur_ctx else "default_org"
        t_user = cur_ctx.tenant_user_id if cur_ctx else "dev_user"

        if reported_usage:
            p_toks = reported_usage.get("prompt_tokens", 0)
            if exec_ctx is not None and role == "react":
                exec_ctx.measured_prompt_tokens = p_toks
            c_toks = reported_usage.get("completion_tokens", 0)
            cache_read = reported_usage.get("cache_read_tokens", 0)
            cache_creation = reported_usage.get("cache_creation_tokens", 0)
        else:
            p_toks, c_toks = estimated_usage(
                eff_model, messages, tools_for_request, stream.produced_text()
            )
            cache_read = 0
            cache_creation = 0

        try:
            if hasattr(self._gateway, "record_usage") and not getattr(
                self._gateway, "records_usage", False
            ):
                await self._gateway.record_usage(
                    tenant_org_id=t_org,
                    tenant_user_id=t_user,
                    model=eff_model,
                    prompt_tokens=p_toks,
                    completion_tokens=c_toks,
                    cache_read_tokens=cache_read,
                    cache_creation_tokens=cache_creation,
                )
        except Exception:
            # Telemetry logging failure should not fail agent execution
            pass

        # Internal per-turn telemetry for cost attribution: the turn handler
        # folds these into the trace's `tokens` object. It is never published to the event
        # bus, so it adds no WebSocket frame.
        step_cost = (
            stream.reported_cost
            if stream.reported_cost is not None
            else estimate_cost_usd(eff_model, p_toks, c_toks, cache_read, cache_creation)
        )
        await self._check_cache_collapse(exec_ctx, p_toks, cache_read, session_id, event_queue)
        await event_queue.put(
            AgentEvent(
                event_type="model_usage",
                session_id=session_id,
                payload={
                    "model": eff_model,
                    "role": role,
                    "prompt_tokens": p_toks,
                    "completion_tokens": c_toks,
                    "cache_read_tokens": cache_read,
                    "cache_creation_tokens": cache_creation,
                    "cost_usd": step_cost,
                    "finish_reason": finish_reason or stream.finish_reason or "stop",
                    "duration_ms": int((time.monotonic() - stream.started_at) * 1000),
                    "reasoning_chars": len(stream.reasoning),
                    "overhead_tokens": self._overhead_tokens(
                        eff_model, messages, tools_for_request
                    ),
                },
            )
        )
        return step_cost

    def _overhead_tokens(
        self, model: str, messages: list[ChatMessage], tools: list[ToolDefinition]
    ) -> int:
        """What every request of the turn pays before the conversation: system prompt and tools.

        Counted once per distinct prompt and tool set, since tokenizing 80 schemas on every
        step would cost more than the figure is worth.
        """
        system = [m for m in messages if m.role == "system"]
        key = (model, sum(len(m.content or "") for m in system), tuple(t.name for t in tools))
        cached = self._overhead_cache.get(key)
        if cached is None:
            cached = request_tokens(model, system, tools)
            if len(self._overhead_cache) > 64:
                self._overhead_cache.clear()
            self._overhead_cache[key] = cached
        return cached

    @staticmethod
    async def _check_cache_collapse(
        exec_ctx: ExecutionContext | None,
        p_toks: int,
        cache_read: int,
        session_id: str,
        event_queue: asyncio.Queue[AgentEvent | None],
    ) -> None:
        """Alert when cache hit rate collapses below 20% across 3 consecutive calls (G4)."""
        if exec_ctx is None or p_toks < 2000:
            return
        cache_ratio = cache_read / p_toks
        if cache_ratio < 0.20:
            exec_ctx.consecutive_low_cache_calls += 1
            if exec_ctx.consecutive_low_cache_calls == 3:
                await event_queue.put(
                    AgentEvent(
                        event_type="thought",
                        session_id=session_id,
                        payload={
                            "delta": (
                                "\n\n[Alert: Cache Collapse Detected — 3 consecutive calls "
                                f"had < 20% prompt cache hit rate ({cache_ratio:.1%}). "
                                "Prefix stability degraded.]\n\n"
                            ),
                            "is_reasoning": True,
                        },
                    )
                )
        else:
            exec_ctx.consecutive_low_cache_calls = 0

    async def _execute_single_tool(
        self,
        session_id: str,
        tc: dict[str, Any],
        event_queue: asyncio.Queue[AgentEvent | None],
    ) -> tuple[str, str, str, int]:
        return await self.dispatcher.execute_single_tool(session_id, tc, event_queue)

    async def _execute_tool_calls(
        self,
        session_id: str,
        tool_calls_raw: dict[int, dict[str, Any]],
        event_queue: asyncio.Queue[AgentEvent | None],
        messages: list[ChatMessage],
    ) -> None:
        await self.dispatcher.execute_tool_calls(session_id, tool_calls_raw, event_queue, messages)

    @staticmethod
    def _build_seed_messages(
        parts: SystemPrompt | None, fallback_text: str | None
    ) -> list[ChatMessage]:
        """The one leading system message of every request: the session-stable prompt.

        The volatile part goes after the conversation instead (``_TurnRun.context_note``): a
        request is cached up to the first byte that differs, so anything that changes from turn
        to turn placed before the history would make the whole history uncacheable.
        """
        prompt = parts or SystemPrompt.from_text(fallback_text)
        if not prompt.stable:
            return []
        return [ChatMessage(role="system", content=prompt.stable, cacheable=True)]

    def _make_summarizer(self, model: str, tools: list[ToolDefinition] | None = None) -> Summarizer:
        return model_summarizer(self._gateway, model, tools)

    async def _read_working_memory(
        self, session_id: str, active_repo: str = ""
    ) -> tuple[str | None, str | None]:
        driver = getattr(self._registry, "driver", None)
        if driver is None or not hasattr(driver, "read_file"):
            return None, None
        plan_md, progress_md = None, None
        repo_slug = active_repo.rstrip("/").split("/")[-1] if active_repo else ""
        plan_paths = (
            [f"{repo_slug}/.rocket/PLAN.md", ".rocket/PLAN.md"]
            if repo_slug
            else [".rocket/PLAN.md"]
        )
        prog_paths = (
            [f"{repo_slug}/.rocket/PROGRESS.md", ".rocket/PROGRESS.md"]
            if repo_slug
            else [".rocket/PROGRESS.md"]
        )
        for p in plan_paths:
            try:
                plan_md = await driver.read_file(session_id, p)
                if plan_md and plan_md.strip():
                    plan_md = plan_md[:50000]
                    break
            except Exception:
                pass
        for p in prog_paths:
            try:
                progress_md = await driver.read_file(session_id, p)
                if progress_md and progress_md.strip():
                    progress_md = progress_md[:50000]
                    break
            except Exception:
                pass
        return plan_md, progress_md

    async def _prepare_dispatch_messages_async(
        self,
        model: str,
        summary_model: str,
        seed_messages: list[ChatMessage],
        conversation: list[ChatMessage],
        whitelisted_tools: list[str] | None = None,
        force_compaction: bool = False,
        run: _TurnRun | None = None,
    ) -> list[ChatMessage]:
        """Compose the request payload with model-summarized compaction when needed."""
        messages = [*seed_messages, *conversation]
        exec_ctx = current_execution_ctx.get()
        tools = self._tools_for_request(whitelisted_tools)
        # The live request only helps on the turn's own model: another model has no cache of it.
        summarize = self._make_summarizer(summary_model, tools if summary_model == model else None)
        step = run.step if run else None
        last_step = run.last_compaction_step if run else None
        active_repo = exec_ctx.active_repository if exec_ctx else ""
        plan_md, progress_md = None, None
        if run is not None:
            # Read once, then again only after a step that wrote them: compaction needs the
            # files, but reading them from the sandbox before every model call cost several
            # sandbox round trips per step.
            if run.working_memory is None:
                run.working_memory = await self._read_working_memory(run.session_id, active_repo)
            plan_md, progress_md = run.working_memory

        if run and isinstance(conversation, RecordingList) and conversation.pending:
            await self._flush_transcript(run.session_id, run.org_id, conversation)

        cleaned = await self.context_manager.enforce_hygiene_async(
            model,
            messages,
            summarize=summarize,
            tools=tools,
            measured_tokens=exec_ctx.measured_prompt_tokens if exec_ctx else 0,
            overrides=exec_ctx.policy.generation.context_windows if exec_ctx else (),
            step=step,
            last_compaction_step=last_step,
            force_compaction=force_compaction,
            working_plan=plan_md,
            progress_md=progress_md,
            target_tokens=exec_ctx.policy.generation.context_target_tokens if exec_ctx else 0,
        )
        if cleaned != messages:
            if exec_ctx is not None:
                exec_ctx.measured_prompt_tokens = 0  # measured before compaction
            del conversation[:]
            conversation.extend(cleaned[len(seed_messages) :])

            if getattr(cleaned, "compacted", False) and run is not None:
                run.last_compaction_step = run.step
                await self._record_and_emit_compaction(run, cleaned)

            return [*seed_messages, *conversation]
        return messages

    async def _record_and_emit_compaction(self, run: _TurnRun, result: Any) -> None:
        boundary_seq = 0
        if self.transcript_store is not None:
            latest_seq = await self.transcript_store.latest_seq(run.session_id, run.org_id)
            tail_count = getattr(result, "tail_count", None)
            if tail_count is None:
                tail_count = len(result) - len(getattr(result, "compacted_prefix", []))
            boundary_seq = max(0, (latest_seq or 0) - max(0, tail_count))

        if self.compaction_store is not None:
            try:
                checkpoint = CompactionCheckpoint(
                    id=f"cmp_{uuid.uuid4().hex[:24]}",
                    session_id=run.session_id,
                    org_id=run.org_id,
                    boundary_seq=boundary_seq,
                    summary=getattr(result, "summary", None),
                    summary_status=getattr(result, "summary_status", "") or "legacy",
                    compacted_messages=getattr(result, "compacted_prefix", []),
                    tokens_before=getattr(result, "tokens_before", 0),
                    tokens_after=getattr(result, "tokens_after", 0),
                    created_at=int(time.time()),
                )
                await self.compaction_store.record_compaction(checkpoint)
            except Exception:
                logger.exception("Failed to record compaction checkpoint for %s", run.session_id)

        await run.event_queue.put(
            AgentEvent(
                event_type="context_compacted",
                session_id=run.session_id,
                payload={
                    "tokens_before": getattr(result, "tokens_before", 0),
                    "tokens_after": getattr(result, "tokens_after", 0),
                    "compacted_messages_count": getattr(result, "compacted_count", 0),
                    "summary": getattr(result, "summary", None),
                    "summary_status": getattr(result, "summary_status", "") or "legacy",
                },
            )
        )

    def _allowed_tools(self, whitelisted_tools: list[str] | None) -> list[ToolDefinition]:
        """Every tool the turn may use, loaded or not."""
        ctx = current_execution_ctx.get()
        denied = ctx.denied_tools if ctx is not None else frozenset()
        all_tools = [t for t in self._registry.get_tool_definitions() if t.name not in denied]
        if whitelisted_tools is None or "*" in whitelisted_tools:
            return all_tools
        listed = set(whitelisted_tools)
        allowed = [tool for tool in all_tools if tool.name in listed]
        loading = ctx.policy.tool_loading if ctx is not None else None
        needed = {"load_guidance"}  # already out of ``all_tools`` for a turn without guidance
        if loading is not None and deferred_names(
            allowed, loading, self._registry.enabled_for_this_turn
        ):
            # A deferred tool is only usable through the loading tools, whatever the agent's list says.
            needed |= LOADING_TOOLS
        if ctx is not None and ctx.plan_mode:
            needed.add("exit_plan_mode")
        # An agent's list never takes away what a platform gate sends it to (ESSENTIAL_TOOLS).
        allowed += [
            t for t in all_tools if t.name in needed & ESSENTIAL_TOOLS and t.name not in listed
        ]
        return allowed

    def _tools_for_request(self, whitelisted_tools: list[str] | None) -> list[ToolDefinition]:
        """The definitions a request carries: allowed tools, without deferred unloaded ones.

        Tools are ordered so request k extends request k-1 with appended entries only,
        preserving the provider's prompt cache across tool loads.
        """
        allowed = self._allowed_tools(whitelisted_tools)
        ctx = current_execution_ctx.get()
        if ctx is None or not ctx.deferred_tools:
            return [t for t in allowed if not self._registry.is_deferred(t.name)]

        allowed_map = {t.name: t for t in allowed}
        base_tools = [t for t in allowed if t.name not in ctx.deferred_tools]
        base_names = {t.name for t in base_tools}
        appended_tools = [
            allowed_map[name]
            for name in getattr(ctx, "loaded_tools_order", [])
            if name in allowed_map and name not in base_names
        ]
        return [*base_tools, *appended_tools]

    def _defer_tools(
        self,
        exec_ctx: ExecutionContext,
        whitelisted_tools: list[str] | None,
        system_prompt: str | None,
        system_prompt_parts: SystemPrompt | None,
    ) -> tuple[str | None, SystemPrompt | None]:
        """Decide which allowed tools wait for ``load_tools`` and list them in the prompt.

        The list names every deferred tool, loaded or not, so it does not change as the agent
        loads tools and the cached prompt prefix stays valid for the session.
        """
        loading = exec_ctx.policy.tool_loading
        allowed = self._allowed_tools(whitelisted_tools)
        deferred = deferred_names(allowed, loading, self._registry.enabled_for_this_turn)
        exec_ctx.deferred_tools = deferred
        exec_ctx.loaded_tools = set(loading.loaded) & deferred
        exec_ctx.loaded_tools_order = [n for n in loading.loaded if n in deferred]
        self._settle_guidance(exec_ctx, allowed)
        self._settle_skills(exec_ctx)
        listed = "".join(
            (
                catalogue(deferred),
                render_index(exec_ctx.guidance_by_server.values()),
            )
        )
        if not listed:
            return system_prompt, system_prompt_parts
        if system_prompt_parts is not None:
            return system_prompt, dataclasses.replace(
                system_prompt_parts, stable=f"{system_prompt_parts.stable}\n{listed}"
            )
        return f"{system_prompt or ''}\n{listed}".strip(), None

    def _settle_guidance(self, exec_ctx: ExecutionContext, allowed: list[ToolDefinition]) -> None:
        """Which MCP servers have guidance, and which of it the turn starts with.

        Guidance for servers the turn cannot use is left out; a turn with none loses the
        ``load_guidance`` tool too, so servers without guidance cost no tokens at all.
        """
        guidance = exec_ctx.policy.mcp_guidance
        reachable = servers_of_tools(t.name for t in allowed)
        exec_ctx.guidance_by_server = {
            g.server: g for g in guidance.servers if g.server in reachable
        }
        if not exec_ctx.guidance_by_server:
            exec_ctx.denied_tools = exec_ctx.denied_tools | {"load_guidance"}
            return
        usable = dataclasses.replace(guidance, servers=tuple(exec_ctx.guidance_by_server.values()))
        loaded = servers_of_tools(
            [t.name for t in allowed if t.name not in exec_ctx.deferred_tools]
            + list(exec_ctx.loaded_tools)
        )
        exec_ctx.guidance_in_effect = in_effect_at_turn_start(usable, loaded)

    @staticmethod
    def _settle_skills(exec_ctx: ExecutionContext) -> None:
        """The skills the turn starts with: those the session put in effect and can still use."""
        chosen = skills_at_turn_start(exec_ctx.policy.skills)
        exec_ctx.skill_texts = dict(chosen)
        exec_ctx.skills_in_effect = {name: exec_ctx.policy.skills.read[name] for name in chosen}

    @staticmethod
    def _bounded_observation_summary(
        conversation: list[ChatMessage], max_items: int = 6, max_chars: int = 400
    ) -> str:
        """Compact digest of the latest tool observations, for a deterministic fallback answer."""
        observations = [m.content for m in conversation if m.role == "tool" and m.content]
        blocks = []
        for index, text in enumerate(observations[-max_items:], start=1):
            snippet = " ".join(str(text).split())[:max_chars]
            if snippet:
                blocks.append(f"{index}. {snippet}")
        return "\n".join(blocks)

    async def _synthesize_final_answer(self, run: _TurnRun) -> str:
        """Force a plain-text final answer, retrying then falling back deterministically.

        Called both when the turn budget is exhausted and after a mid-turn error. The
        provider is given ``tool_choice="none"`` so a tool-happy model cannot answer with
        yet another tool call, and an empty completion is never reported as success.
        """

        async def _attempt() -> str:
            messages = await self._prepare_dispatch_messages_async(
                run.react_model, run.react_model, run.seed_messages, run.conversation, run=run
            )
            next_notes = list(run.next_notes)
            run.next_notes = []
            req_messages = assemble_request_messages(
                messages,
                context_note=run.context_note,
                sticky_notes=run.sticky_notes,
                next_notes=next_notes,
                turn_images=run.exec_ctx.turn_images if run.exec_ctx else (),
                is_synthetic=run.synthetic_prompt,
            )
            reply = await self._stream_model_step(
                run.session_id,
                req_messages,
                run.event_queue,
                model=run.react_model,
                whitelisted_tools=None,
                role="react",
                tool_choice="none",
            )
            return reply.text.strip()

        text = await _attempt()
        if text:
            return text

        run.next_notes.append(
            "Your previous response was empty or attempted a tool call. Respond now "
            "with PLAIN TEXT ONLY: a complete, direct final answer to the user's "
            "request, using the information already gathered. Do not call any tool."
        )
        text = await _attempt()
        if text:
            return text

        # Deterministic fallback so the turn never completes with a blank answer.
        observations = self._bounded_observation_summary(run.conversation)
        fallback = (
            f"I reached the {run.turn_limit}-step limit before producing a final answer. "
            "Here is what I gathered so far:\n\n" + (observations or "(no tool observations)")
        )
        await run.event_queue.put(
            AgentEvent(
                event_type="thought",
                session_id=run.session_id,
                payload={"delta": fallback, "is_reasoning": False},
            )
        )
        return fallback

    @staticmethod
    def _attach_to_current_user_turn(
        messages: list[ChatMessage], attachments: list[AttachmentRef]
    ) -> list[ChatMessage]:
        """Clone the latest user message with attachments, leaving history untouched.

        The attachment bytes belong to *this* turn only. Mutating the stored conversation
        would re-send them on every subsequent ReAct step and later turn, which both wastes
        tokens and — because they land before the dynamic suffix — breaks the provider's
        cached prefix. The clone is request-local; the conversation keeps the plain message.
        """
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].role == "user":
                original = messages[index]
                return [
                    *messages[:index],
                    ChatMessage(role="user", content=original.content, attachments=attachments),
                    *messages[index + 1 :],
                ]
        return messages

    async def _run_react_cycle(
        self,
        session_id: str,
        user_message: str,
        event_queue: asyncio.Queue[AgentEvent | None],
        model: str | None = None,
        system_prompt: str | None = None,
        system_prompt_parts: SystemPrompt | None = None,
        whitelisted_tools: list[str] | None = None,
        exec_ctx: ExecutionContext | None = None,
        max_turns: int | None = None,
        attachments: list[AttachmentRef] | None = None,
        model_router: ModelRouterProtocol | None = None,
        synthetic_prompt: bool = False,
    ) -> None:
        if exec_ctx is not None:
            current_execution_ctx.set(exec_ctx)
        run = await self._begin_turn_run(
            session_id=session_id,
            user_message=user_message,
            event_queue=event_queue,
            model=model,
            system_prompt=system_prompt,
            system_prompt_parts=system_prompt_parts,
            whitelisted_tools=whitelisted_tools,
            exec_ctx=exec_ctx,
            max_turns=max_turns,
            attachments=attachments,
            model_router=model_router,
            synthetic_prompt=synthetic_prompt,
        )
        self._live_turns.add(session_id)
        try:
            await self._drive_turn(run, user_message, synthetic_prompt=synthetic_prompt)
        except Exception as err:
            await self._fail_turn(run, err)
        finally:
            await self._return_untaken_steering(run)
            await self._flush_transcript(run.session_id, run.org_id, run.conversation)
            self._evict_idle_sessions()
            await event_queue.put(None)

    async def _begin_turn_run(
        self,
        *,
        session_id: str,
        user_message: str,
        event_queue: asyncio.Queue[AgentEvent | None],
        model: str | None,
        system_prompt: str | None,
        system_prompt_parts: SystemPrompt | None,
        whitelisted_tools: list[str] | None,
        exec_ctx: ExecutionContext | None,
        max_turns: int | None,
        attachments: list[AttachmentRef] | None,
        model_router: ModelRouterProtocol | None,
        synthetic_prompt: bool = False,
    ) -> _TurnRun:
        """Resolve the models, load the conversation and compose the seed messages."""
        effective_model = model or self.default_model
        # The ReAct loop (and every synthesis/failure step) runs on the routing table's
        # "react" model; under balanced/economy modes this can differ from the base model.
        react_model = (
            model_router.model_for("react") if model_router is not None else effective_model
        )
        summary_model = (
            model_router.model_for("summarize") if model_router is not None else react_model
        )
        # Session history holds *conversation only*. The system prompt is composed
        # fresh on every dispatch so its cacheable prefix stays byte-stable while
        # the volatile suffix (thread context, telemetry) may change per turn.
        org_id = exec_ctx.tenant_org_id if exec_ctx is not None else "default_org"
        conversation = await self._conversation_for(session_id, org_id)
        # A previous turn may have been cut off between a tool call and its result.
        conversation[:] = repair_tool_pairs(conversation)
        seed_messages = self._build_seed_messages(system_prompt_parts, system_prompt)
        # Recall happens once at turn start. Memories are written over time, so they belong
        # with the volatile context at the end of the request, never in the cached prefix.
        recall_prompt = user_message
        if synthetic_prompt:
            last_real = _current_request(conversation)
            if last_real is not None:
                content = conversation[last_real].content
                if content:
                    recall_prompt = content
        memory_context = await self._registry.build_memory_context(recall_prompt)
        workspace_context = await self._registry.build_workspace_context()
        dynamic = system_prompt_parts.dynamic if system_prompt_parts is not None else ""
        working_memory = await self._read_working_memory(
            session_id, exec_ctx.active_repository if exec_ctx is not None else ""
        )
        context = [
            part.strip()
            for part in (
                dynamic,
                self._guidance_note(exec_ctx, conversation),
                memory_context if isinstance(memory_context, str) else "",
                workspace_context if isinstance(workspace_context, str) else "",
                _working_memory_note(*working_memory),
            )
            if part and part.strip()
        ]
        att_list = list(attachments or [])
        non_img_atts = [a for a in att_list if not (a.mime and a.mime.startswith("image/"))]
        if non_img_atts:
            manifest = format_attachment_manifest(non_img_atts)
            if manifest:
                context.append(manifest)
        return _TurnRun(
            session_id=session_id,
            org_id=org_id,
            conversation=conversation,
            seed_messages=seed_messages,
            react_model=react_model,
            summary_model=summary_model,
            turn_limit=max_turns if max_turns is not None else self.max_turns,
            event_queue=event_queue,
            whitelisted_tools=whitelisted_tools,
            exec_ctx=exec_ctx,
            pending_attachments=att_list,
            context_note="\n\n".join(context),
            synthetic_prompt=synthetic_prompt,
            working_memory=working_memory,
        )

    @staticmethod
    def _guidance_note(exec_ctx: ExecutionContext | None, conversation: list[ChatMessage]) -> str:
        """The standing instructions the turn starts with, rendered from the session's state:
        the guidance of MCP servers and the skills the agent put in effect."""
        if exec_ctx is None:
            return ""
        parts: list[str] = []
        if exec_ctx.guidance_in_effect:
            parts.append(
                render_note(
                    exec_ctx.guidance_by_server[server]
                    for server in exec_ctx.guidance_in_effect
                    if server in exec_ctx.guidance_by_server
                )
            )
        # Skills live in the conversation (the execute_skill result, re-attached by compaction);
        # the note only carries those it lacks, and says which were edited since they were read.
        missing = skills_missing_from(exec_ctx.skill_texts, [m.content or "" for m in conversation])
        if missing:
            parts.append(render_skills(missing))
        parts.append(changed_skills_note(changed_skills(exec_ctx.policy.skills)))
        return "\n\n".join(part for part in parts if part)

    @staticmethod
    def _add_pending_guidance(run: _TurnRun) -> None:
        """Move what the agent put in effect this step (MCP guidance, skills, a cloned
        repository's instructions) into the turn's context note, where the next request carries
        it and later turns rebuild it from the session's state."""
        ctx = run.exec_ctx
        if ctx is None or not (ctx.guidance_pending or ctx.standing_pending):
            return
        added = [render_note(ctx.guidance_pending, added=True), *ctx.standing_pending]
        ctx.guidance_pending, ctx.standing_pending = [], []
        extra = "\n\n".join(part for part in added if part)
        run.context_note = f"{run.context_note}\n\n{extra}".strip()

    async def _emit(self, run: _TurnRun, event_type: str, payload: dict[str, Any]) -> None:
        await run.event_queue.put(
            AgentEvent(event_type=event_type, session_id=run.session_id, payload=payload)
        )

    async def _emit_status(
        self,
        run: _TurnRun,
        state: OrchestratorState,
        status: str,
        message: str,
        **extra: Any,
    ) -> None:
        await self._emit(
            run, "status", {"state": state.value, "status": status, "message": message, **extra}
        )

    async def _drive_turn(
        self, run: _TurnRun, user_message: str, synthetic_prompt: bool = False
    ) -> None:
        """Run ReAct steps until the model answers, the user cancels or the budget ends."""
        opener = (
            automatic_turn_prompt(user_message)
            if synthetic_prompt
            else ChatMessage(role="user", content=user_message)
        )
        run.conversation.append(opener)
        await self._flush_transcript(run.session_id, run.org_id, run.conversation)
        await self._emit_status(run, OrchestratorState.RUNNING, "running", "Thinking...")

        # Nudge the model to converge before the hard cap so the final answer is
        # written within budget rather than forced out at the wall (spec 8.1.2). Without a cap
        # there is nothing to converge towards: the turn runs until the model answers.
        limited = run.turn_limit != NO_STEP_LIMIT
        soft_warn_at = max(1, run.turn_limit - max(1, run.turn_limit // 5)) if limited else -1
        for step in range(run.turn_limit) if limited else itertools.count():
            if self._cancel_flags.get(run.session_id, False):
                self._session_states[run.session_id] = OrchestratorState.CANCELLED.value
                return
            if await self._react_step(run, step, soft_warn_at):
                return
        await self._synthesize_after_budget(run)

    def is_running(self, session_id: str) -> bool:
        """Whether a turn of ``session_id`` runs in this process."""
        return session_id in self._live_turns

    def withdraw_steering(self, session_id: str, message_id: str) -> None:
        """Drop a steering message the turn has not taken yet (the person withdrew it)."""
        pending = self._steering.get(session_id)
        if pending:
            self._steering[session_id] = [m for m in pending if m.message_id != message_id]

    def steer(self, session_id: str, message: SteeringMessage) -> bool:
        if session_id not in self._live_turns:
            return False
        self._steering.setdefault(session_id, []).append(message)
        return True

    async def _take_steering(self, run: _TurnRun) -> None:
        """Add what the person sent during the turn to the conversation, before the next step."""
        taken = self._steering.pop(run.session_id, [])
        for message in taken:
            run.conversation.append(
                ChatMessage(
                    role="user", content=message.content, author_user_id=message.author_user_id
                )
            )
            await self._emit(run, "user_steer", _steering_payload(message))
        if taken:
            await self._flush_transcript(run.session_id, run.org_id, run.conversation)

    async def _return_untaken_steering(self, run: _TurnRun) -> None:
        """Messages that arrived as the turn ended: the host starts them as the next turn."""
        self._live_turns.discard(run.session_id)
        untaken = self._steering.pop(run.session_id, [])
        if untaken:
            await self._emit(
                run, "steering_untaken", {"messages": [_steering_payload(m) for m in untaken]}
            )

    async def _react_step(self, run: _TurnRun, step: int, soft_warn_at: int) -> bool:
        """One model call and the tools it asked for; True once the turn is complete."""
        run.step = step
        await self._take_steering(run)
        if step == soft_warn_at:
            run.sticky_notes.append(
                f"Budget notice: {run.turn_limit - step} of {run.turn_limit} reasoning "
                "steps remain. Begin converging now - stop opening new lines of "
                "investigation and be ready to deliver a complete final answer."
            )
        if reminder := stale_notice(run.conversation):
            run.next_notes.append(reminder)
        plan_mode = run.exec_ctx is not None and run.exec_ctx.plan_mode
        if pace := pace_notice(run.conversation, may_edit=not plan_mode):
            run.next_notes.append(pace)
        try:
            reply = await self._model_step(run)
        except ContextWindowExceededError:
            if run.compacted_for_overflow:
                raise
            # The estimate was short of the provider's count: compact now and try once more.
            run.compacted_for_overflow = True
            logger.warning("Context window exceeded in %s; compacting", run.session_id)
            reply = await self._model_step(run, force_compaction=True)
        run.step_costs.append(reply.cost_usd)
        run.cumulative_cost_usd += reply.cost_usd
        cost_cap = run.exec_ctx.policy.max_turn_cost_usd if run.exec_ctx else None
        if cost_cap is not None and run.cumulative_cost_usd >= cost_cap:
            logger.warning(
                "Turn cost limit ($%.2f) reached in %s; stopping at step boundary",
                cost_cap,
                run.session_id,
            )
            self._session_states[run.session_id] = OrchestratorState.COMPLETED.value
            await self._emit_status(
                run,
                OrchestratorState.COMPLETED,
                "completed",
                f"Turn cost limit (${cost_cap:.2f}) reached.",
            )
            return True
        tool_calls, cut = split_truncated(reply.tool_calls, reply.finish_reason)
        # Beside real calls, call markup in the text is a provider's leftover, not something
        # the model said: kept, it showed as a step's intent and reached the answer.
        assistant_text = (
            strip_leaked_calls(reply.text)
            if tool_calls and contains_leaked_calls(reply.text)
            else reply.text
        )
        if cut:
            logger.warning("A reply in %s was cut off at the token limit", run.session_id)
            run.next_notes.append(truncation_notice(cut))
            if not tool_calls:
                return False
        if not tool_calls and contains_leaked_calls(assistant_text):
            return await self._handle_leaked_calls(run, assistant_text)
        if not tool_calls and not (assistant_text or "").strip():
            return await self._handle_empty_reply(run, reply.finish_reason)
        run.empty_retries = 0
        if not tool_calls:
            run.conversation.append(
                ChatMessage(
                    role="assistant",
                    content=assistant_text,
                    reasoning=reply.reasoning or None,
                    reasoning_blocks=reply.reasoning_blocks or None,
                )
            )
            # Once per turn: ask the model to reconcile unfinished items or verify edits,
            # but never reject on the final step so the substantive answer is never lost to budget exhaustion.
            if (
                not run.checklist_reminded
                and not run.on_last_step(step)
                and (reminder := unfinished_notice(run.conversation))
            ):
                run.checklist_reminded = True
                run.next_notes.append(reminder)
                await self._flush_transcript(run.session_id, run.org_id, run.conversation)
                return False
            if (
                not run.verification_reminded
                and not run.on_last_step(step)
                and (reminder := verification_gate_notice(run.conversation))
            ):
                run.verification_reminded = True
                run.next_notes.append(reminder)
                await self._flush_transcript(run.session_id, run.org_id, run.conversation)
                return False
            self._session_states[run.session_id] = OrchestratorState.COMPLETED.value
            await self._emit_status(run, OrchestratorState.COMPLETED, "completed", "Turn complete.")
            return True
        if assistant_text and assistant_text.strip():
            await self._emit_status(
                run, OrchestratorState.RUNNING, "running", assistant_text.strip()
            )
        await self.dispatcher.execute_tool_calls(
            run.session_id,
            tool_calls,
            run.event_queue,
            run.conversation,
            text=assistant_text,
            reasoning=reply.reasoning,
            reasoning_blocks=reply.reasoning_blocks,
        )
        return await self._after_tool_calls(run, tool_calls)

    async def _after_tool_calls(self, run: _TurnRun, tool_calls: dict[int, dict[str, Any]]) -> bool:
        """Record a step's tool results; True when a ``wait_for`` ended the turn."""
        await self._process_tool_results_progress(run, tool_calls)
        self._add_pending_guidance(run)
        if any(_mentions_working_memory(call) for call in tool_calls.values()):
            run.working_memory = None
        await self._flush_transcript(run.session_id, run.org_id, run.conversation)
        if self._wait_registered(run, tool_calls):
            self._session_states[run.session_id] = OrchestratorState.WAITING.value
            await self._emit_status(
                run,
                OrchestratorState.WAITING,
                "waiting",
                "Turn entered waiting state for external condition.",
            )
            return True
        return False

    @staticmethod
    def _wait_registered(run: _TurnRun, tool_calls: dict[int, dict[str, Any]]) -> bool:
        """Whether this step registered a wait that will wake the session (and is reset).

        Without an execution context (tests driving the loop directly) any ``wait_for`` call
        counts, as it always did.
        """
        ctx = run.exec_ctx
        if ctx is None:
            return any(call.get("name") == "wait_for" for call in tool_calls.values())
        registered, ctx.wait_registered = ctx.wait_registered, False
        return registered

    async def _process_tool_results_progress(
        self,
        run: _TurnRun,
        tool_calls: dict[int, dict[str, Any]] | list[dict[str, Any]],
    ) -> None:
        """Inspect completed tool calls to detect failure and edit/revert loops."""
        calls_list = list(tool_calls.values()) if isinstance(tool_calls, dict) else list(tool_calls)
        for call in calls_list:
            name = str(call.get("name") or (call.get("function") or {}).get("name") or "")
            if not name:
                continue
            raw_args = str(
                call.get("arguments") or (call.get("function") or {}).get("arguments") or "{}"
            )
            result_msg = next(
                (
                    m
                    for m in reversed(run.conversation)
                    if m.role == "tool" and m.tool_call_id == call.get("id")
                ),
                None,
            )
            res_content = (result_msg.content or "") if result_msg else ""
            detection = self._check_progress_loop(run, name, raw_args, res_content)
            if detection is not None:
                if detection.level == 1:
                    run.next_notes.append(detection.suggested_nudge)
                elif detection.level == 2:
                    await self._handle_progress_decision(run, detection)
                    return

    def _check_progress_loop(
        self, run: _TurnRun, name: str | None, raw_args: str, output: str
    ) -> LoopDetectionResult | None:
        if not name:
            return None
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else {}
        except Exception:
            args = {}

        if name == "bash_exec":
            cmd = str(args.get("command", ""))
            exit_code = 0
            out_lower = output.lower()
            if "exit code " in out_lower:
                try:
                    idx = out_lower.index("exit code ")
                    exit_code = int(output[idx + 10 :].split()[0].rstrip(":.,"))
                except Exception:
                    exit_code = 1
            elif (
                output.startswith("Error:")
                or "command failed" in out_lower
                or "exit status" in out_lower
            ):
                exit_code = 1
            return run.progress_tracker.record_command(cmd, exit_code)

        if name in ("file_edit", "file_write", "apply_patch", "apply_diff_file"):
            path = str(
                args.get("path")
                or args.get("file_path")
                or args.get("target_file")
                or args.get("patch_path")
                or ""
            )
            content = str(
                args.get("content")
                or args.get("replacement_block")
                or args.get("patch")
                or args.get("diff")
                or args.get("patch_content")
                or args.get("diff_content")
                or output
            )
            if path:
                return run.progress_tracker.record_file_edit(path, content)

        if name in ("update_task_checklist", "update_checklist", "checklist_update"):
            tasks = args.get("tasks")
            if isinstance(tasks, list):
                run.active_checklist = tasks

        return None

    async def _handle_progress_decision(self, run: _TurnRun, res: LoopDetectionResult) -> None:
        """Present an interactive decision card when progress is stuck in a loop."""
        mode = run.exec_ctx.policy.permission_mode if run.exec_ctx and run.exec_ctx.policy else ""
        if mode != "ask":
            # Only a session that asked to approve actions is paused for a person: autonomous
            # sessions run for hours unattended, and a question there parks the agent until
            # someone happens to look. They get the warning and change course themselves.
            run.next_notes.append(
                f"[Progress warning: {res.reason} Stop repeating what has not worked: diagnose "
                "the cause or take another approach, and if you are blocked say so in your "
                "answer.]"
            )
            return

        question_text = (
            res.question_text or "The task appears stuck. How would you like to proceed?"
        )
        options = res.options or ["Try a different approach", "Abort turn"]
        question_id = f"q_loop_{uuid.uuid4().hex[:8]}"
        q_obj = InteractiveQuestion(
            question_id=question_id,
            question_text=question_text,
            options=options,
            is_multi_select=False,
            default_recommended_option=options[0],
        )
        wait_event = asyncio.Event()
        self.gates.register_question(question_id, run.session_id, wait_event, q_obj)
        self._session_states[run.session_id] = OrchestratorState.PAUSED_FOR_INPUT.value
        await run.event_queue.put(
            AgentEvent(
                event_type="interactive_question",
                session_id=run.session_id,
                payload=asdict(q_obj),
            )
        )
        try:
            await self._wait_for_human(wait_event)
        finally:
            answer = self.gates.forget_question(question_id)

        self._session_states[run.session_id] = OrchestratorState.RUNNING.value
        selected_options, custom_text = answer
        chosen = (
            selected_options[0] if selected_options else (custom_text or "Try a different approach")
        )
        if "abort" in chosen.lower():
            self._cancel_flags[run.session_id] = True
            run.next_notes.append(
                "The user requested to abort this turn due to repeated loop detection."
            )
        else:
            run.next_notes.append(
                f"[User Direction: {chosen}. Stop repeating the previous action and change strategy.]"
            )

    async def _model_step(self, run: _TurnRun, force_compaction: bool = False) -> _ModelReply:
        """Build one ReAct request (hygiene, attachments, notes) and stream the model's reply."""
        messages = await self._prepare_dispatch_messages_async(
            run.react_model,
            run.summary_model,
            run.seed_messages,
            run.conversation,
            whitelisted_tools=run.whitelisted_tools,
            force_compaction=force_compaction,
            run=run,
        )
        req_messages = assemble_request_messages(
            messages,
            context_note=run.context_note,
            sticky_notes=run.sticky_notes,
            next_notes=run.next_notes,
            turn_images=run.exec_ctx.turn_images if run.exec_ctx else (),
            pending_attachments=run.pending_attachments,
            is_synthetic=run.synthetic_prompt,
        )
        reply = await self._stream_model_step(
            run.session_id,
            req_messages,
            run.event_queue,
            model=run.react_model,
            whitelisted_tools=run.whitelisted_tools,
            role="react",
        )
        # Consumed only once a request went through, so a retry carries them again.
        run.pending_attachments, run.next_notes = [], []
        return reply

    async def _handle_leaked_calls(self, run: _TurnRun, assistant_text: str) -> bool:
        """The model wrote tool calls as text: ask for a real call, or say so and stop.

        The markup is kept out of the conversation, so it neither confuses the next request nor
        reaches a person as an answer. Returns True once the turn is over.
        """
        if run.leak_retries < MAX_LEAK_RETRIES:
            run.leak_retries += 1
            logger.warning(
                "Model %s wrote tool calls as text in %s (retry %d)",
                run.react_model,
                run.session_id,
                run.leak_retries,
            )
            run.next_notes.append(LEAK_NOTICE)
            return False
        said = strip_leaked_calls(assistant_text)
        answer = f"{said}\n\n{LEAK_GIVE_UP}".strip()
        await run.event_queue.put(
            AgentEvent(
                event_type="thought",
                session_id=run.session_id,
                payload={"delta": LEAK_GIVE_UP, "is_reasoning": False},
            )
        )
        run.conversation.append(ChatMessage(role="assistant", content=answer))
        self._session_states[run.session_id] = OrchestratorState.COMPLETED.value
        await self._emit_status(run, OrchestratorState.COMPLETED, "completed", "Turn complete.")
        return True

    async def _handle_empty_reply(self, run: _TurnRun, finish_reason: str | None) -> bool:
        """No text and no tool call: tell the model and continue, or explain and stop.

        Returns True once the turn is over. The empty reply is not kept in the conversation.
        """
        if run.empty_retries < MAX_EMPTY_RETRIES:
            run.empty_retries += 1
            logger.warning(
                "Model %s returned an empty reply in %s (finish_reason=%s, retry %d)",
                run.react_model,
                run.session_id,
                finish_reason,
                run.empty_retries,
            )
            remaining = (
                max(0, run.turn_limit - run.step) if run.turn_limit != NO_STEP_LIMIT else None
            )
            run.next_notes.append(empty_reply_notice(finish_reason, remaining_steps=remaining))
            return False
        answer = empty_reply_give_up(finish_reason)
        await run.event_queue.put(
            AgentEvent(
                event_type="thought",
                session_id=run.session_id,
                payload={"delta": answer, "is_reasoning": False},
            )
        )
        run.conversation.append(ChatMessage(role="assistant", content=answer))
        self._session_states[run.session_id] = OrchestratorState.COMPLETED.value
        await self._emit_status(run, OrchestratorState.COMPLETED, "completed", "Turn complete.")
        return True

    async def _synthesize_after_budget(self, run: _TurnRun) -> None:
        """The turn budget ran out while tools were still being dispatched: answer anyway."""
        limit = run.turn_limit
        await self._emit(
            run,
            "post_failure_synthesis",
            {
                "reason": f"Maximum turn limit reached ({limit} turns). Synthesizing final answer from gathered context.",
                "max_turns": limit,
                "turns_taken": limit,
            },
        )
        await self._emit_status(
            run,
            OrchestratorState.RUNNING,
            "running",
            f"Turn limit reached ({limit} turns). Synthesizing final answer...",
        )
        run.sticky_notes.append(
            f"The maximum allowed turn limit ({limit} turns) has been reached. "
            "Do NOT attempt to call any more tools in this turn. Based on all gathered "
            "investigation results, code readings, commands, and observations in the "
            "conversation so far, synthesize a comprehensive, clear, and direct final answer "
            "addressing the user's request."
        )
        text = await self._synthesize_final_answer(run)
        run.conversation.append(ChatMessage(role="assistant", content=text))
        self._session_states[run.session_id] = OrchestratorState.COMPLETED.value
        await self._emit_status(
            run,
            OrchestratorState.COMPLETED,
            "completed",
            "Turn complete (post-turn-budget synthesis).",
        )

    async def _fail_turn(self, run: _TurnRun, err: Exception) -> None:
        """Report the failure and, where tool results exist, salvage a partial answer."""
        self._session_states[run.session_id] = OrchestratorState.ERROR.value
        await self._emit_status(
            run,
            OrchestratorState.ERROR,
            "error",
            f"Turn execution error: {err}",
            error=str(err),
        )
        # A dead provider would fail the synthesis call too, so only other failures
        # (tool or protocol errors) attempt to salvage a partial answer.
        if isinstance(err, ModelExecutionError) or not any(
            m.role == "tool" for m in run.conversation
        ):
            return
        try:
            await self._emit(
                run,
                "post_failure_synthesis",
                {
                    "reason": f"Execution halted due to error: {err}. Synthesizing partial findings.",
                    "error": str(err),
                },
            )
            run.sticky_notes.append(
                f"An error occurred during turn execution: {err}. "
                "Do NOT call any tools in this turn. Synthesize the findings and progress "
                "gathered so far."
            )
            text = await self._synthesize_final_answer(run)
            run.conversation.append(ChatMessage(role="assistant", content=text))
        except Exception:
            pass

    async def submit_question_answer(
        self,
        session_id: str,
        question_id: str,
        selected_options: list[str],
        custom_text: str | None = None,
    ) -> None:
        try:
            self.gates.submit_question_answer(
                session_id, question_id, selected_options, custom_text
            )
        except KeyError:
            relay = self.cluster_relay
            if relay is None or not await relay.forward_question(
                session_id, question_id, selected_options, custom_text
            ):
                raise

    async def submit_human_approval(
        self,
        session_id: str,
        action_id: str,
        approved: bool,
    ) -> None:
        try:
            self.gates.submit_human_approval(session_id, action_id, approved)
        except KeyError:
            relay = self.cluster_relay
            if relay is None or not await relay.forward_approval(session_id, action_id, approved):
                raise

    async def cancel_turn(self, session_id: str, *, propagate: bool = True) -> None:
        """Halt the session's turn here and, unless this call came from a peer, everywhere."""
        if propagate and self.cluster_relay is not None:
            await self.cluster_relay.broadcast_cancel(session_id)
        self._cancel_flags[session_id] = True
        self._session_states[session_id] = OrchestratorState.CANCELLED.value
        self.gates.cancel_session_gates(session_id)

        task = self._active_tasks.get(session_id)
        if task and not task.done():
            task.cancel()
