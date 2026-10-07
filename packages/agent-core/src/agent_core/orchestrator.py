"""Async ReAct Orchestrator implementing AgentOrchestratorProtocol with modular subsystems."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from enum import Enum
from typing import Any

from agent_core.context import ContextManager, ExecutionContext, current_execution_ctx
from agent_core.dispatcher import ToolDispatcher
from agent_core.interaction import DecisionGateManager
from agent_core.subagents import SubagentRunner
from agent_core.tools.registry import ToolRegistry
from specifications.interfaces.agent import (
    AgentEvent,
    AgentOrchestratorProtocol,
    FileDiff,
    InteractiveQuestion,
    SubagentConfig,
    SubagentTaskRequest,
)
from specifications.interfaces.llm import ChatMessage, LLMGatewayProtocol, ModelRequest


class OrchestratorState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED_FOR_INPUT = "paused_for_input"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class AsyncReActOrchestrator(AgentOrchestratorProtocol):
    """
    Autonomous ReAct execution engine managing reasoning, tool dispatch,
    and interactive human-in-the-loop decision gating.
    """

    PAUSED_FOR_INPUT = OrchestratorState.PAUSED_FOR_INPUT.value
    RUNNING = OrchestratorState.RUNNING.value
    IDLE = OrchestratorState.IDLE.value
    COMPLETED = OrchestratorState.COMPLETED.value
    CANCELLED = OrchestratorState.CANCELLED.value

    def __init__(
        self,
        gateway: LLMGatewayProtocol,
        registry: ToolRegistry,
        default_model: str = "openrouter/deepseek/deepseek-v4.1-flash",
        max_turns: int = 15,
        subagent_configs: dict[str, SubagentConfig] | None = None,
    ) -> None:
        self._gateway: LLMGatewayProtocol = gateway
        self._registry: ToolRegistry = registry
        self.default_model: str = default_model
        self.max_turns: int = max_turns

        self.subagent_runner = SubagentRunner(
            gateway=gateway, registry=registry, configs=subagent_configs
        )
        self._registry.bind_subagent_runner(self.subagent_runner)

        # Decomposed modular subsystems
        self.dispatcher = ToolDispatcher(registry=registry)
        self.gates = DecisionGateManager()
        self.context_manager = ContextManager()

        self._session_histories: dict[str, list[ChatMessage]] = {}
        self._session_states: dict[str, str] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._active_tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def gateway(self) -> LLMGatewayProtocol:
        """Expose LLM gateway instance for follow-up suggestions and direct model queries."""
        return self._gateway

    # Backward-compatibility properties for tests inspecting internal state
    @property
    def _file_locks(self) -> dict[str, asyncio.Lock]:
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

    def _setup_registry_callbacks(
        self,
        session_id: str,
        event_queue: asyncio.Queue[AgentEvent | None],
        parent_model: str | None = None,
    ) -> None:
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

            await wait_event.wait()

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
            sub_res = await self.subagent_runner.run(
                request=sub_req,
                session_id=session_id,
                event_queue=event_queue,
                default_model=parent_model or self.default_model,
            )
            return (
                f"[{sub_res.role.upper()} SUBAGENT REPORT]\n"
                f"Task: {sub_res.task}\n"
                f"Status: {sub_res.status} ({sub_res.turns_taken} turns, {sub_res.tool_calls_count} tools, {sub_res.duration_ms}ms)\n\n"
                f"{sub_res.summary}"
            )

        self._registry.set_diff_callback(_on_diff)
        self._registry.set_question_callback(_on_question)
        self._registry.set_plan_callback(_on_plan)
        self._registry.set_subagent_callback(_on_subagent)

    def process_user_turn(
        self,
        session_id: str,
        user_message: str,
        model: str | None = None,
        system_prompt: str | None = None,
        whitelisted_tools: list[str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        effective_model = model or self.default_model

        # Wire contextvars execution context for multi-session concurrency safety
        exec_ctx = ExecutionContext(
            session_id=session_id,
            event_queue=event_queue,
            parent_model=effective_model,
        )
        current_execution_ctx.set(exec_ctx)

        self._setup_registry_callbacks(
            session_id,
            event_queue,
            parent_model=effective_model,
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
                whitelisted_tools=whitelisted_tools,
                exec_ctx=exec_ctx,
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
    ) -> tuple[str, dict[int, dict[str, Any]]]:
        all_tools = self._registry.get_tool_definitions()
        if whitelisted_tools is not None and "*" not in whitelisted_tools:
            allowed_set = set(whitelisted_tools)
            tools_for_request = [t for t in all_tools if t.name in allowed_set]
        else:
            tools_for_request = all_tools

        request = ModelRequest(
            model=model or self.default_model,
            messages=messages,
            tools=tools_for_request,
        )

        assistant_text = ""
        tool_calls_raw: dict[int, dict[str, Any]] = {}

        async for chunk in self._gateway.chat_stream(request):
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

    async def _run_react_cycle(
        self,
        session_id: str,
        user_message: str,
        event_queue: asyncio.Queue[AgentEvent | None],
        model: str | None = None,
        system_prompt: str | None = None,
        whitelisted_tools: list[str] | None = None,
        exec_ctx: ExecutionContext | None = None,
    ) -> None:
        if exec_ctx is not None:
            current_execution_ctx.set(exec_ctx)

        effective_model = model or self.default_model

        try:
            if session_id not in self._session_histories:
                self._session_histories[session_id] = []
            history = self._session_histories[session_id]

            if system_prompt:
                if not history or history[0].role != "system":
                    history.insert(0, ChatMessage(role="system", content=system_prompt))
                else:
                    history[0] = ChatMessage(role="system", content=system_prompt)

            history.append(ChatMessage(role="user", content=user_message))

            await event_queue.put(
                AgentEvent(
                    event_type="status",
                    session_id=session_id,
                    payload={
                        "state": OrchestratorState.RUNNING.value,
                        "status": "running",
                        "message": "Thinking...",
                    },
                )
            )

            for _ in range(self.max_turns):
                if self._cancel_flags.get(session_id, False):
                    self._session_states[session_id] = OrchestratorState.CANCELLED.value
                    break

                # Apply progressive context hygiene before dispatching to model
                cleaned_history = self.context_manager.enforce_hygiene(effective_model, history)
                if len(cleaned_history) != len(history) or cleaned_history != history:
                    history.clear()
                    history.extend(cleaned_history)

                assistant_text, tool_calls = await self._stream_model_step(
                    session_id,
                    history,
                    event_queue,
                    model=effective_model,
                    whitelisted_tools=whitelisted_tools,
                )

                if not tool_calls:
                    history.append(ChatMessage(role="assistant", content=assistant_text))
                    self._session_states[session_id] = OrchestratorState.COMPLETED.value
                    await event_queue.put(
                        AgentEvent(
                            event_type="status",
                            session_id=session_id,
                            payload={
                                "state": OrchestratorState.COMPLETED.value,
                                "status": "completed",
                                "message": "Turn complete.",
                            },
                        )
                    )
                    break

                await self._execute_tool_calls(session_id, tool_calls, event_queue, history)

        except Exception as err:
            self._session_states[session_id] = OrchestratorState.ERROR.value
            await event_queue.put(
                AgentEvent(
                    event_type="status",
                    session_id=session_id,
                    payload={
                        "state": OrchestratorState.ERROR.value,
                        "status": "error",
                        "error": str(err),
                        "message": f"Turn execution error: {err}",
                    },
                )
            )
        finally:
            await event_queue.put(None)

    async def submit_question_answer(
        self,
        session_id: str,
        question_id: str,
        selected_options: list[str],
        custom_text: str | None = None,
    ) -> None:
        self.gates.submit_question_answer(session_id, question_id, selected_options, custom_text)

    async def submit_human_approval(
        self,
        session_id: str,
        action_id: str,
        approved: bool,
    ) -> None:
        self.gates.submit_human_approval(session_id, action_id, approved)

    async def cancel_turn(self, session_id: str) -> None:
        self._cancel_flags[session_id] = True
        self._session_states[session_id] = OrchestratorState.CANCELLED.value
        self.gates.cancel_session_gates(session_id)

        task = self._active_tasks.get(session_id)
        if task and not task.done():
            task.cancel()
