"""
Lightweight Subagent Runner executing scoped sub-delegations
with bounded turn limits, specialized system prompts, and tool isolation.
"""

import asyncio
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from llm_gateway.tokens import estimated_usage

from agent_core.context import current_execution_ctx
from agent_core.dispatcher import ToolDispatcher
from agent_core.tools.registry import ToolRegistry
from agent_core.truncation import split_truncated, truncation_notice
from specifications.interfaces.agent import (
    AgentEvent,
    SubagentConfig,
    SubagentTaskRequest,
    SubagentTaskResult,
)
from specifications.interfaces.llm import (
    ChatMessage,
    LLMGatewayProtocol,
    ModelExecutionError,
    ModelRequest,
    ToolDefinition,
)

# Searching a codebase takes several steps before reading: four left no room to report.
DEFAULT_SUBAGENT_TURNS = 12
# Inspection only: what the read-only roles, and a role nobody configured, may use.
READ_ONLY_TOOLS = [
    "file_read", "grep_search", "glob_files", "list_dir", "code_search", "web_search", "web_fetch",
]  # fmt: skip
EDIT_OR_COMMAND_TOOLS = frozenset(
    {"file_write", "file_edit", "apply_patch", "apply_diff_file", "bash_exec", "run_tests"}
)
FINAL_REPORT_NOTE = (
    "You have used all your steps. Do not call tools: report now what you found, what you "
    "could not check, and what the main agent should do next."
)

DEFAULT_SUBAGENT_CONFIGS: dict[str, SubagentConfig] = {
    "security_auditor": SubagentConfig(
        id="security_auditor",
        name="Security & Penetration Auditor",
        role_title="Application Security Specialist",
        description="Audits source code for injection attacks, RLS boundary leaks, secret exposure, and permissions.",
        system_prompt=(
            "You are a dedicated Security Auditor subagent. Inspect source files and configuration for "
            "vulnerabilities (OWASP Top 10, SQL injection, RLS bypasses, secret leakage). "
            "Synthesize your findings concisely with specific line references and suggested remediations."
        ),
        whitelisted_tools=[*READ_ONLY_TOOLS],
        max_turns=DEFAULT_SUBAGENT_TURNS,
    ),
    "qa_verifier": SubagentConfig(
        id="qa_verifier",
        name="QA & Test Verifier",
        role_title="Automated Test Specialist",
        description="Executes test suites in the sandbox, synthesizes failing cases, and verifies regressions.",
        system_prompt=(
            "You are a QA & Test Verifier subagent. Run the tests (run_tests, or bash_exec for "
            "anything it does not cover), inspect the failures in the code, and report exactly "
            "which tests passed and which failed, with the assertion and the likely cause."
        ),
        whitelisted_tools=["run_tests", "bash_exec", "file_read", "grep_search", "list_dir"],
        max_turns=DEFAULT_SUBAGENT_TURNS,
    ),
    "researcher": SubagentConfig(
        id="researcher",
        name="Codebase & Web Researcher",
        role_title="Technical Exploration Specialist",
        description="Explores external documentation, packages, or codebases to answer architectural questions.",
        system_prompt=(
            "You are a Codebase & Web Researcher subagent. Search technical documentation and read files "
            "to answer specific architectural or implementation questions without altering files."
        ),
        whitelisted_tools=[*READ_ONLY_TOOLS],
        max_turns=DEFAULT_SUBAGENT_TURNS,
    ),
}


NO_DELEGATION = frozenset({"delegate_subagent", "delegate_subagents_parallel"})
DEFAULT_MODEL = "openrouter/deepseek/deepseek-v4.1-flash"


def _fallback_config(role_key: str) -> SubagentConfig:
    """The model invented a role: never hand an unconfigured delegate the full toolset."""
    return SubagentConfig(
        id=role_key,
        name=f"{role_key.replace('_', ' ').title()} Specialist",
        role_title="Specialized Delegate",
        description="Custom delegated subagent",
        system_prompt=f"You are a specialized {role_key} subagent. Complete the requested task efficiently.",
        whitelisted_tools=list(READ_ONLY_TOOLS),
        max_turns=DEFAULT_SUBAGENT_TURNS,
    )


@dataclass
class _Generation:
    """What one model call produced."""

    text: str = ""
    tool_calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    usage: dict[str, int] | None = None
    finish_reason: str | None = None

    def produced_text(self) -> str:
        return self.text + "".join(call["arguments"] for call in self.tool_calls.values())

    def add_tool_call_delta(self, delta: dict[str, Any]) -> None:
        index = delta.get("index", 0)
        call = self.tool_calls.setdefault(
            index, {"id": delta.get("id"), "name": "", "arguments": ""}
        )
        function = delta.get("function") or {}
        if function.get("name"):
            call["name"] = function["name"]
        if function.get("arguments"):
            call["arguments"] += function["arguments"]


class SubagentRunner:
    """Manages the lifecycle and bounded execution of specialized subagents."""

    def __init__(
        self,
        gateway: LLMGatewayProtocol,
        registry: ToolRegistry,
        configs: dict[str, SubagentConfig] | None = None,
        dispatcher: ToolDispatcher | None = None,
    ) -> None:
        self.gateway = gateway
        self.registry = registry
        # Shared with the main turn, so a delegate's calls count against the same limits.
        self.dispatcher = dispatcher or ToolDispatcher(registry=registry)
        self.configs: dict[str, SubagentConfig] = configs or dict(DEFAULT_SUBAGENT_CONFIGS)

    async def run(
        self,
        request: SubagentTaskRequest,
        session_id: str,
        event_queue: asyncio.Queue[AgentEvent | None] | None = None,
        default_model: str = DEFAULT_MODEL,
    ) -> SubagentTaskResult:
        """Executes a bounded ReAct sub-cycle for the delegated subagent."""
        start_time = time.monotonic()
        subagent_id = request.subagent_id or f"sub_{uuid.uuid4().hex[:6]}"
        role_key = request.role.lower().strip()
        config = self._configured_roles().get(role_key)

        if config is not None and not config.enabled:
            return SubagentTaskResult(
                role=config.id,
                task=request.task,
                summary=f"The '{config.id}' subagent is disabled by the organization.",
                status="failed",
                subagent_id=subagent_id,
            )

        execution = _SubagentExecution(
            runner=self,
            request=request,
            session_id=session_id,
            event_queue=event_queue,
            config=config or _fallback_config(role_key),
            subagent_id=subagent_id,
            model=(config.model if config else None) or default_model,
        )
        await execution.announce_started()
        await execution.run_turns()
        duration_ms = max(1, int((time.monotonic() - start_time) * 1000))
        result = execution.result(duration_ms)
        await execution.announce_completed(result)
        return result

    def _configured_roles(self) -> Mapping[str, SubagentConfig]:
        """The organization's roles for this turn, else the runner's own."""
        turn_ctx = current_execution_ctx.get()
        if turn_ctx is not None and turn_ctx.callbacks.subagent_configs is not None:
            return turn_ctx.callbacks.subagent_configs
        return self.configs

    def tools_for(self, config: SubagentConfig) -> list[ToolDefinition]:
        """The tool definitions a delegate may use; subagents may not delegate further."""
        all_tools = self.registry.get_tool_definitions()
        if "*" in config.whitelisted_tools:
            return [tool for tool in all_tools if tool.name not in NO_DELEGATION]
        allowed = set(config.whitelisted_tools) - NO_DELEGATION
        return [tool for tool in all_tools if tool.name in allowed]


class _SubagentExecution:
    """One delegated task: its private conversation, tool whitelist and counters."""

    def __init__(
        self,
        *,
        runner: SubagentRunner,
        request: SubagentTaskRequest,
        session_id: str,
        event_queue: asyncio.Queue[AgentEvent | None] | None,
        config: SubagentConfig,
        subagent_id: str,
        model: str,
    ) -> None:
        self._runner = runner
        self._request = request
        self._session_id = session_id
        self._queue = event_queue
        self._config = config
        self._subagent_id = subagent_id
        self._model = model
        self._tools = runner.tools_for(config)
        self._permitted = {tool.name for tool in self._tools}
        self._messages: list[ChatMessage] = [
            ChatMessage(
                role="system",
                content=f"{config.system_prompt}\n\n{workspace_brief(self._permitted)}",
            ),
            ChatMessage(
                role="user",
                content=(
                    f"Delegated Task: {request.task}\n"
                    f"{f'Context: {request.context}' if request.context else ''}\n\n"
                    "Provide a thorough, concrete, and synthesized result upon completion."
                ),
            ),
        ]
        self._turns_taken = 0
        self._tool_calls_count = 0
        self._final_summary = ""

    async def run_turns(self) -> None:
        for _turn in range(self._config.max_turns):
            self._turns_taken += 1
            try:
                generation = await self._generate()
            except Exception as err:
                self._final_summary = f"Subagent error during generation: {err}"
                return
            if generation.text:
                self._final_summary = generation.text
            generation.tool_calls, cut = split_truncated(
                generation.tool_calls, generation.finish_reason
            )
            if cut:
                self._messages.append(ChatMessage(role="user", content=truncation_notice(cut)))
                if not generation.tool_calls:
                    continue
            if not generation.tool_calls:
                return
            await self._run_tool_calls(generation)
        await self._final_report()

    async def _final_report(self) -> None:
        """The steps ran out while the delegate was still working: one tool-less call turns
        what it gathered into a report instead of returning its last half-thought."""
        self._messages.append(ChatMessage(role="user", content=FINAL_REPORT_NOTE))
        try:
            generation = await self._generate(with_tools=False)
        except Exception as err:
            self._final_summary = f"Subagent error during its final report: {err}"
            return
        if generation.text:
            self._final_summary = generation.text

    def result(self, duration_ms: int) -> SubagentTaskResult:
        return SubagentTaskResult(
            role=self._config.id,
            task=self._request.task,
            summary=self._final_summary.strip() or "Task completed with no further comments.",
            status="completed",
            turns_taken=self._turns_taken,
            tool_calls_count=self._tool_calls_count,
            duration_ms=duration_ms,
            subagent_id=self._subagent_id,
        )

    async def announce_started(self) -> None:
        await self._emit(
            "subagent_started",
            {
                "subagent_id": self._subagent_id,
                "role": self._config.id,
                "name": self._config.name,
                "task": self._request.task,
            },
        )

    async def announce_completed(self, result: SubagentTaskResult) -> None:
        await self._emit(
            "subagent_completed",
            {
                "subagent_id": self._subagent_id,
                "role": self._config.id,
                "task": self._request.task,
                "summary": result.summary,
                "duration_ms": result.duration_ms,
                "turns_taken": result.turns_taken,
                "tool_calls_count": result.tool_calls_count,
            },
        )

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._queue:
            await self._queue.put(
                AgentEvent(event_type=event_type, session_id=self._session_id, payload=payload)
            )

    async def _emit_progress(self, delta: str, *, is_reasoning: bool) -> None:
        await self._emit(
            "subagent_progress",
            {"subagent_id": self._subagent_id, "delta": delta, "is_reasoning": is_reasoning},
        )

    async def _generate(self, with_tools: bool = True) -> _Generation:
        turn_ctx = current_execution_ctx.get()
        model_request = ModelRequest(
            model=self._model,
            messages=self._messages,
            tools=self._tools if with_tools else None,
            temperature=self._config.temperature,
            # Subagent requests inherit the turn's model data-governance requirement.
            zero_retention=turn_ctx.zero_retention if turn_ctx else False,
            region=turn_ctx.region if turn_ctx else "",
        )
        generation = _Generation()
        async for chunk in self._runner.gateway.chat_stream(model_request):
            if chunk.error:
                raise ModelExecutionError(chunk.error)
            if chunk.token_usage:
                generation.usage = chunk.token_usage
            if chunk.finish_reason:
                generation.finish_reason = chunk.finish_reason
            if chunk.reasoning_delta:
                await self._emit_progress(chunk.reasoning_delta, is_reasoning=True)
            if chunk.text_delta:
                generation.text += chunk.text_delta
                await self._emit_progress(chunk.text_delta, is_reasoning=False)
            if chunk.tool_call_delta:
                generation.add_tool_call_delta(chunk.tool_call_delta)
        await self._record_usage(generation)
        return generation

    async def _record_usage(self, generation: _Generation) -> None:
        """Book the delegate's tokens against the same ledger as the main turn."""
        gateway = self._runner.gateway
        if not hasattr(gateway, "record_usage") or getattr(gateway, "records_usage", False):
            return
        try:
            ctx = current_execution_ctx.get()
            if generation.usage:
                prompt_tokens = generation.usage.get("prompt_tokens", 0)
                completion_tokens = generation.usage.get("completion_tokens", 0)
            else:
                prompt_tokens, completion_tokens = estimated_usage(
                    self._model, self._messages, self._tools, generation.produced_text()
                )
            await gateway.record_usage(
                tenant_org_id=ctx.tenant_org_id if ctx else "default_org",
                tenant_user_id=ctx.tenant_user_id if ctx else "dev_user",
                model=self._model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except Exception:
            # Accounting must never fail the delegated task itself.
            pass

    async def _run_tool_calls(self, generation: _Generation) -> None:
        calls = list(generation.tool_calls.values())
        self._messages.append(
            ChatMessage(
                role="assistant",
                content=generation.text,
                tool_calls=[
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call["arguments"]},
                    }
                    for call in calls
                ],
            )
        )
        for call in calls:
            self._tool_calls_count += 1
            self._messages.append(
                ChatMessage(
                    role="tool",
                    content=await self._execute(call),
                    tool_call_id=call.get("id") or str(uuid.uuid4().hex[:8]),
                )
            )

    async def _execute(self, call: dict[str, Any]) -> str:
        tool_name = call.get("name") or "unknown"
        raw_args = call.get("arguments") or "{}"
        args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args else {}
        if tool_name not in self._permitted:
            # Advertising a whitelist is not enforcement: the model can name any tool.
            return (
                f"Blocked: the '{tool_name}' tool is not available to the "
                f"'{self._config.id}' subagent."
            )
        dispatcher = self._runner.dispatcher
        try:
            return await dispatcher.run_guarded(self._session_id, tool_name, args)
        except TimeoutError:
            return f"Tool execution timed out after {dispatcher.tool_timeout_seconds}s"
        except Exception as err:
            return f"Tool execution error: {err}"


def workspace_brief(permitted: set[str]) -> str:
    """How the sandbox works, for a delegate that starts without the main turn's context."""
    ctx = current_execution_ctx.get()
    active = ctx.active_repository if ctx is not None else ""
    lines = [
        "Workspace: repositories are cloned into /workspace/<name>.",
        (
            f"The active repository is {active}: relative paths and commands use it."
            if active
            else "No repository is active: use absolute /workspace/... paths."
        ),
        "Each bash_exec runs in a fresh shell (no cd or exports carry over).",
        "Tool output is clamped: search (grep_search, glob_files, code_search) before reading, "
        "and read large files by line range.",
    ]
    if not permitted & EDIT_OR_COMMAND_TOOLS:
        lines.append(
            "Your tools are read-only: do not try to change anything. Report what you found; "
            "the main agent makes the changes."
        )
    return "\n".join(lines)
