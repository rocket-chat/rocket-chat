"""Async ReAct Orchestrator implementing AgentOrchestratorProtocol with modular subsystems."""

import asyncio
import logging
import os
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any

from agent_core.context import (
    ContextManager,
    ExecutionContext,
    Summarizer,
    current_execution_ctx,
    render_transcript,
)
from agent_core.dispatcher import ToolDispatcher
from agent_core.interaction import DecisionGateManager
from agent_core.subagents import SubagentRunner
from agent_core.tools.registry import ToolRegistry
from agent_core.transcript import RecordingList, repair_tool_pairs
from specifications.interfaces.agent import (
    APPROVAL_GRANTED_PREFIX,
    AgentEvent,
    AgentOrchestratorProtocol,
    ApprovalRequest,
    ClusterRelayProtocol,
    FileDiff,
    GenerationParams,
    InteractiveQuestion,
    SubagentConfig,
    SubagentTaskRequest,
    SystemPrompt,
    TurnCallbacks,
    TurnPolicy,
)
from specifications.interfaces.llm import (
    AttachmentRef,
    ChatMessage,
    LLMGatewayProtocol,
    ModelExecutionError,
    ModelRequest,
    ModelRouterProtocol,
    ModelScope,
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
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


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


class AsyncReActOrchestrator(AgentOrchestratorProtocol):
    """
    Autonomous ReAct execution engine managing reasoning, tool dispatch,
    and interactive human-in-the-loop decision gating.
    """

    PAUSED_FOR_INPUT = OrchestratorState.PAUSED_FOR_INPUT.value
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
        max_turns: int = 30,
        subagent_configs: dict[str, SubagentConfig] | None = None,
        transcript_store: TranscriptStoreProtocol | None = None,
    ) -> None:
        self._gateway: LLMGatewayProtocol = gateway
        self._registry: ToolRegistry = registry
        self.default_model: str = default_model
        env_max = (
            os.getenv("AGENT_MAX_TURNS") or os.getenv("ROCKET_MAX_TURNS") or os.getenv("MAX_TURNS")
        )
        self.max_turns: int = int(env_max) if env_max and env_max.isdigit() else max_turns
        self.fallback_models: list[str] = [
            m.strip() for m in (os.getenv("ROCKET_FALLBACK_MODELS") or "").split(",") if m.strip()
        ]

        self.subagent_runner = SubagentRunner(
            gateway=gateway, registry=registry, configs=subagent_configs
        )
        self._registry.bind_subagent_runner(self.subagent_runner)

        # Decomposed modular subsystems
        self.dispatcher = ToolDispatcher(registry=registry)
        self.gates = DecisionGateManager()
        # Set by multi-replica deployments so decisions can reach the replica running the turn.
        self.cluster_relay: ClusterRelayProtocol | None = None
        # Wraps every wait on a human decision so the host can release scarce resources
        # (the process-wide turn slot) while the turn is parked.
        self.human_wait_scope: Callable[[], AbstractAsyncContextManager[None]] | None = None
        self.context_manager = ContextManager()

        # The durable log is the source of truth; this is a bounded cache of live
        # conversations, rehydrated from the store when a session is not resident.
        self.transcript_store: TranscriptStoreProtocol | None = transcript_store
        self.max_cached_sessions: int = int(os.getenv("ROCKET_MAX_CACHED_SESSIONS", "256") or 256)
        self._session_histories: OrderedDict[str, list[ChatMessage]] = OrderedDict()
        self._session_states: dict[str, str] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._active_tasks: dict[str, asyncio.Task[None]] = {}

    async def _wait_for_human(self, wait_event: asyncio.Event) -> None:
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
                loaded = await self.transcript_store.load(session_id, org_id)
            except Exception:
                # Starting cold is better than failing the turn; the failure is logged.
                logger.exception("Transcript rehydration failed for session %s", session_id)
        conversation = RecordingList(repair_tool_pairs(loaded))
        self._session_histories[session_id] = conversation
        return conversation

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

            await self._wait_for_human(wait_event)

            if self._cancel_flags.get(session_id, False):
                return "Execution was cancelled by the user."

            selected_options, custom_text = self.gates.get_question_answer(question_id)
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

            await self._wait_for_human(wait_event)

            if self._cancel_flags.get(session_id, False):
                return "Approval aborted: execution was cancelled by the user."

            approved = self.gates.get_approval_decision(action_id)
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
            zero_retention=zero_retention,
            region=region,
            enabled_toolsets=enabled_toolsets or frozenset(),
            plan_mode=(policy or TurnPolicy()).permission_mode == "plan",
            policy=policy or TurnPolicy(),
            sandbox_capabilities=(policy.sandbox_capabilities if policy is not None else None),
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
            )
        )

        self._cancel_flags[session_id] = False
        self._session_states[session_id] = OrchestratorState.RUNNING.value

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

    async def _stream_model_step(
        self,
        session_id: str,
        messages: list[ChatMessage],
        event_queue: asyncio.Queue[AgentEvent | None],
        model: str | None = None,
        whitelisted_tools: list[str] | None = None,
        role: str = "react",
        tool_choice: str | None = None,
    ) -> tuple[str, dict[int, dict[str, Any]]]:
        all_tools = self._registry.get_tool_definitions()
        if whitelisted_tools is not None and "*" not in whitelisted_tools:
            allowed_set = set(whitelisted_tools)
            tools_for_request = [t for t in all_tools if t.name in allowed_set]
        else:
            tools_for_request = all_tools

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

        assistant_text = ""
        tool_calls_raw: dict[int, dict[str, Any]] = {}

        reported_usage: dict[str, int] | None = None
        async for chunk in self._gateway.chat_stream(request):
            if chunk.error:
                raise ModelExecutionError(chunk.error)
            if chunk.token_usage:
                reported_usage = chunk.token_usage
            if chunk.reasoning_delta:
                await event_queue.put(
                    AgentEvent(
                        event_type="thought",
                        session_id=session_id,
                        payload={"delta": chunk.reasoning_delta, "is_reasoning": True},
                    )
                )
                await asyncio.sleep(0)
            if chunk.text_delta:
                assistant_text += chunk.text_delta
                await event_queue.put(
                    AgentEvent(
                        event_type="thought",
                        session_id=session_id,
                        payload={"delta": chunk.text_delta, "is_reasoning": False},
                    )
                )
                await asyncio.sleep(0)
            if chunk.tool_call_delta:
                idx = chunk.tool_call_delta.get("index", 0)
                if idx not in tool_calls_raw:
                    tool_calls_raw[idx] = {
                        "id": chunk.tool_call_delta.get("id"),
                        "name": "",
                        "arguments": "",
                    }
                func = chunk.tool_call_delta.get("function") or {}
                if func.get("name"):
                    tool_calls_raw[idx]["name"] = func["name"]
                if func.get("arguments"):
                    tool_calls_raw[idx]["arguments"] += func["arguments"]

        # Record usage telemetry into gateway ledger for org and user accounting
        cur_ctx = current_execution_ctx.get()
        t_org = cur_ctx.tenant_org_id if cur_ctx else "default_org"
        t_user = cur_ctx.tenant_user_id if cur_ctx else "dev_user"
        eff_model = model or self.default_model

        if reported_usage:
            p_toks = reported_usage.get("prompt_tokens", 0)
            c_toks = reported_usage.get("completion_tokens", 0)
            cache_read = reported_usage.get("cache_read_tokens", 0)
            cache_creation = reported_usage.get("cache_creation_tokens", 0)
        else:
            # Estimate: approximate tokens from messages and assistant text if provider omitted usage
            p_toks = max(50, sum(len(m.content or "") // 4 for m in messages))
            c_toks = max(10, len(assistant_text) // 4)
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
                },
            )
        )

        return assistant_text, tool_calls_raw

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
        """Compose the leading system messages for a dispatch.

        The stable prefix is emitted as its own cacheable message so a provider
        cache breakpoint can never include volatile (per-turn) content.
        """
        prompt = parts or SystemPrompt.from_text(fallback_text)
        seed: list[ChatMessage] = []
        if prompt.stable:
            seed.append(ChatMessage(role="system", content=prompt.stable, cacheable=True))
        if prompt.dynamic:
            seed.append(ChatMessage(role="system", content=prompt.dynamic))
        return seed

    def _prepare_dispatch_messages(
        self,
        model: str,
        seed_messages: list[ChatMessage],
        conversation: list[ChatMessage],
    ) -> list[ChatMessage]:
        """Compose the request payload, persisting any compaction the hygiene pass applied."""
        messages = [*seed_messages, *conversation]
        cleaned = self.context_manager.enforce_hygiene(model, messages)
        if cleaned != messages:
            del conversation[:]
            conversation.extend(cleaned[len(seed_messages) :])
            return [*seed_messages, *conversation]
        return messages

    def _make_summarizer(self, model: str) -> Summarizer:
        """A model-backed summarizer for Tier-3 context compaction (cheap `summarize` route)."""

        async def _summarize(middle: list[ChatMessage]) -> str:
            request = ModelRequest(
                model=model,
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "You compress the middle of a long coding-agent session so work can "
                            "continue. Write a dense summary: goal, decisions made, files "
                            "touched, commands run and their outcomes, open problems. Plain "
                            "text, no preamble."
                        ),
                    ),
                    ChatMessage(role="user", content=render_transcript(middle)),
                ],
                max_tokens=700,
                temperature=0.1,
                stream=False,
            )
            text = ""
            async for chunk in self._gateway.chat_stream(request):
                if chunk.error:
                    raise ModelExecutionError(chunk.error)
                if chunk.text_delta:
                    text += chunk.text_delta
            return text

        return _summarize

    async def _prepare_dispatch_messages_async(
        self,
        model: str,
        summary_model: str,
        seed_messages: list[ChatMessage],
        conversation: list[ChatMessage],
    ) -> list[ChatMessage]:
        """Compose the request payload with model-summarized compaction when needed."""
        messages = [*seed_messages, *conversation]
        cleaned = await self.context_manager.enforce_hygiene_async(
            model, messages, summarize=self._make_summarizer(summary_model)
        )
        if cleaned != messages:
            del conversation[:]
            conversation.extend(cleaned[len(seed_messages) :])
            return [*seed_messages, *conversation]
        return messages

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

    async def _synthesize_final_answer(
        self,
        session_id: str,
        event_queue: asyncio.Queue[AgentEvent | None],
        model: str,
        seed_messages: list[ChatMessage],
        conversation: list[ChatMessage],
        turn_limit: int,
    ) -> str:
        """Force a plain-text final answer, retrying then falling back deterministically.

        Called both when the turn budget is exhausted and after a mid-turn error. The
        provider is given ``tool_choice="none"`` so a tool-happy model cannot answer with
        yet another tool call, and an empty completion is never reported as success.
        """

        async def _attempt() -> str:
            messages = await self._prepare_dispatch_messages_async(
                model, model, seed_messages, conversation
            )
            text, _ = await self._stream_model_step(
                session_id,
                messages,
                event_queue,
                model=model,
                whitelisted_tools=None,
                role="react",
                tool_choice="none",
            )
            return text.strip()

        text = await _attempt()
        if text:
            return text

        conversation.append(
            ChatMessage(
                role="system",
                content=(
                    "Your previous response was empty or attempted a tool call. Respond now "
                    "with PLAIN TEXT ONLY: a complete, direct final answer to the user's "
                    "request, using the information already gathered. Do not call any tool."
                ),
            )
        )
        text = await _attempt()
        if text:
            return text

        # Deterministic fallback so the turn never completes with a blank answer.
        observations = self._bounded_observation_summary(conversation)
        fallback = (
            f"I reached the {turn_limit}-step limit before producing a final answer. "
            "Here is what I gathered so far:\n\n" + (observations or "(no tool observations)")
        )
        await event_queue.put(
            AgentEvent(
                event_type="thought",
                session_id=session_id,
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
        )
        try:
            await self._drive_turn(run, user_message)
        except Exception as err:
            await self._fail_turn(run, err)
        finally:
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
        # Recall happens once at turn start. Recovered knowledge is volatile (memories are
        # written over time), so it goes in its own non-cacheable system message *after* the
        # stable prefix - never inside it - keeping the cached prefix byte-identical
        # across turns even for a session with memories.
        memory_context = await self._registry.build_memory_context(user_message)
        if isinstance(memory_context, str) and memory_context.strip():
            seed_messages = [*seed_messages, ChatMessage(role="system", content=memory_context)]
        return _TurnRun(
            session_id=session_id,
            org_id=org_id,
            conversation=conversation,
            seed_messages=seed_messages,
            react_model=react_model,
            summary_model=summary_model,
            turn_limit=max_turns or self.max_turns,
            event_queue=event_queue,
            whitelisted_tools=whitelisted_tools,
            exec_ctx=exec_ctx,
            # Attachments ride the *first* model dispatch of this turn only. Every later
            # ReAct step and every later turn re-sends the plain user message, keeping the
            # attachment bytes out of the cached prefix (spec 8.2.2, invariant 1).
            pending_attachments=list(attachments or []),
        )

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

    async def _drive_turn(self, run: _TurnRun, user_message: str) -> None:
        """Run ReAct steps until the model answers, the user cancels or the budget ends."""
        run.conversation.append(ChatMessage(role="user", content=user_message))
        await self._flush_transcript(run.session_id, run.org_id, run.conversation)
        await self._emit_status(run, OrchestratorState.RUNNING, "running", "Thinking...")

        # Nudge the model to converge before the hard cap so the final answer is
        # written within budget rather than forced out at the wall (spec 8.1.2).
        soft_warn_at = max(1, run.turn_limit - max(1, run.turn_limit // 5))
        for step in range(run.turn_limit):
            if self._cancel_flags.get(run.session_id, False):
                self._session_states[run.session_id] = OrchestratorState.CANCELLED.value
                return
            if await self._react_step(run, step, soft_warn_at):
                return
        await self._synthesize_after_budget(run)

    async def _react_step(self, run: _TurnRun, step: int, soft_warn_at: int) -> bool:
        """One model call and the tools it asked for; True once the turn is complete."""
        if step == soft_warn_at:
            run.conversation.append(
                ChatMessage(
                    role="system",
                    content=(
                        f"Budget notice: {run.turn_limit - step} of {run.turn_limit} reasoning "
                        "steps remain. Begin converging now - stop opening new lines of "
                        "investigation and be ready to deliver a complete final answer."
                    ),
                )
            )
        # Apply progressive context hygiene before dispatching to the model
        messages = await self._prepare_dispatch_messages_async(
            run.react_model, run.summary_model, run.seed_messages, run.conversation
        )
        # The user's files ride the first dispatch only; images the agent asked to
        # see (view_image) ride every dispatch of this turn, and neither is stored.
        in_view = [*run.pending_attachments, *(run.exec_ctx.turn_images if run.exec_ctx else [])]
        if in_view:
            messages = self._attach_to_current_user_turn(messages, in_view)
            run.pending_attachments = []

        assistant_text, tool_calls = await self._stream_model_step(
            run.session_id,
            messages,
            run.event_queue,
            model=run.react_model,
            whitelisted_tools=run.whitelisted_tools,
            role="react",
        )
        if not tool_calls:
            run.conversation.append(ChatMessage(role="assistant", content=assistant_text))
            self._session_states[run.session_id] = OrchestratorState.COMPLETED.value
            await self._emit_status(run, OrchestratorState.COMPLETED, "completed", "Turn complete.")
            return True
        await self._execute_tool_calls(
            run.session_id, tool_calls, run.event_queue, run.conversation
        )
        await self._flush_transcript(run.session_id, run.org_id, run.conversation)
        return False

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
        run.conversation.append(
            ChatMessage(
                role="system",
                content=(
                    f"The maximum allowed turn limit ({limit} turns) has been reached. "
                    "Do NOT attempt to call any more tools. Based on all gathered investigation results, "
                    "code readings, commands, and observations in the conversation so far, "
                    "synthesize a comprehensive, clear, and direct final answer addressing the user's request."
                ),
            )
        )
        text = await self._synthesize_final_answer(
            run.session_id,
            run.event_queue,
            run.react_model,
            run.seed_messages,
            run.conversation,
            limit,
        )
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
            run.conversation.append(
                ChatMessage(
                    role="system",
                    content=(
                        f"An error occurred during turn execution: {err}. "
                        "Do NOT call any tools. Synthesize the findings and progress gathered so far."
                    ),
                )
            )
            text = await self._synthesize_final_answer(
                run.session_id,
                run.event_queue,
                run.react_model,
                run.seed_messages,
                run.conversation,
                run.turn_limit,
            )
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
