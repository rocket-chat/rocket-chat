"""
Lightweight Subagent Runner executing scoped sub-delegations
with bounded turn limits, specialized system prompts, and tool isolation.
"""

import asyncio
import json
import time
import uuid
from typing import Any

from agent_core.context import current_execution_ctx
from agent_core.tools.registry import ToolRegistry
from specifications.interfaces.agent import (
    AgentEvent,
    SubagentConfig,
    SubagentTaskRequest,
    SubagentTaskResult,
)
from specifications.interfaces.llm import ChatMessage, LLMGatewayProtocol, ModelRequest

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
        whitelisted_tools=["file_read", "web_search", "web_fetch"],
        max_turns=4,
    ),
    "qa_verifier": SubagentConfig(
        id="qa_verifier",
        name="QA & Test Verifier",
        role_title="Automated Test Specialist",
        description="Executes test suites in the sandbox, synthesizes failing cases, and verifies regressions.",
        system_prompt=(
            "You are a QA & Test Verifier subagent. Run tests using bash_exec, inspect outputs, "
            "and synthesize an exact diagnostic report showing passed vs failed assertions."
        ),
        whitelisted_tools=["bash_exec", "file_read"],
        max_turns=4,
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
        whitelisted_tools=["file_read", "web_search", "web_fetch"],
        max_turns=4,
    ),
}


class SubagentRunner:
    """Manages the lifecycle and bounded execution of specialized subagents."""

    def __init__(
        self,
        gateway: LLMGatewayProtocol,
        registry: ToolRegistry,
        configs: dict[str, SubagentConfig] | None = None,
    ) -> None:
        self.gateway = gateway
        self.registry = registry
        self.configs: dict[str, SubagentConfig] = configs or dict(DEFAULT_SUBAGENT_CONFIGS)

    async def run(
        self,
        request: SubagentTaskRequest,
        session_id: str,
        event_queue: asyncio.Queue[AgentEvent | None] | None = None,
        default_model: str = "openrouter/deepseek/deepseek-v4.1-flash",
    ) -> SubagentTaskResult:
        """Executes a bounded ReAct sub-cycle for the delegated subagent."""
        start_time = time.monotonic()
        subagent_id = request.subagent_id or f"sub_{uuid.uuid4().hex[:6]}"
        role_key = request.role.lower().strip()
        config = self.configs.get(role_key)

        if not config:
            # Fallback dynamic config
            config = SubagentConfig(
                id=role_key,
                name=f"{role_key.replace('_', ' ').title()} Specialist",
                role_title="Specialized Delegate",
                description="Custom delegated subagent",
                system_prompt=f"You are a specialized {role_key} subagent. Complete the requested task efficiently.",
                whitelisted_tools=["*"],
                max_turns=4,
            )

        if event_queue:
            await event_queue.put(
                AgentEvent(
                    event_type="subagent_started",
                    session_id=session_id,
                    payload={
                        "subagent_id": subagent_id,
                        "role": config.id,
                        "name": config.name,
                        "task": request.task,
                    },
                )
            )

        # Build isolated subagent conversation history
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=config.system_prompt),
            ChatMessage(
                role="user",
                content=(
                    f"Delegated Task: {request.task}\n"
                    f"{f'Context: {request.context}' if request.context else ''}\n\n"
                    "Provide a thorough, concrete, and synthesized result upon completion."
                ),
            ),
        ]

        turns_taken = 0
        tool_calls_count = 0
        final_summary = ""

        # Filter tools for subagent
        all_tools = self.registry.get_tool_definitions()
        if "*" in config.whitelisted_tools:
            # Do NOT allow subagents to call delegate_subagent to prevent infinite recursion
            sub_tools = [t for t in all_tools if not t.name.startswith("delegate_subagent")]
        else:
            allowed = set(config.whitelisted_tools) - {
                "delegate_subagent",
                "delegate_subagents_parallel",
            }
            sub_tools = [t for t in all_tools if t.name in allowed]

        effective_model = config.model or default_model

        for _turn in range(config.max_turns):
            turns_taken += 1
            model_req = ModelRequest(
                model=effective_model,
                messages=messages,
                tools=sub_tools,
                temperature=config.temperature,
            )

            assistant_text = ""
            tool_calls_raw: dict[int, dict[str, Any]] = {}

            reported_usage: dict[str, int] | None = None
            try:
                async for chunk in self.gateway.chat_stream(model_req):
                    if chunk.token_usage:
                        reported_usage = chunk.token_usage
                    if chunk.reasoning_delta:
                        if event_queue:
                            await event_queue.put(
                                AgentEvent(
                                    event_type="subagent_progress",
                                    session_id=session_id,
                                    payload={
                                        "subagent_id": subagent_id,
                                        "delta": chunk.reasoning_delta,
                                        "is_reasoning": True,
                                    },
                                )
                            )
                    if chunk.text_delta:
                        assistant_text += chunk.text_delta
                        if event_queue:
                            await event_queue.put(
                                AgentEvent(
                                    event_type="subagent_progress",
                                    session_id=session_id,
                                    payload={
                                        "subagent_id": subagent_id,
                                        "delta": chunk.text_delta,
                                        "is_reasoning": False,
                                    },
                                )
                            )
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

                # Record subagent usage into ledger
                try:
                    cur_ctx = current_execution_ctx.get()
                    t_org = cur_ctx.tenant_org_id if cur_ctx else "default_org"
                    t_user = cur_ctx.tenant_user_id if cur_ctx else "dev_user"
                    if reported_usage:
                        p_toks = reported_usage.get("prompt_tokens", 0)
                        c_toks = reported_usage.get("completion_tokens", 0)
                    else:
                        p_toks = max(50, sum(len(m.content or "") // 4 for m in messages))
                        c_toks = max(10, len(assistant_text) // 4)

                    if hasattr(self.gateway, "record_usage"):
                        await self.gateway.record_usage(
                            tenant_org_id=t_org,
                            tenant_user_id=t_user,
                            model=effective_model,
                            prompt_tokens=p_toks,
                            completion_tokens=c_toks,
                        )
                except Exception:
                    pass
            except Exception as e:
                final_summary = f"Subagent error during generation: {e}"
                break

            if assistant_text:
                final_summary = assistant_text

            if not tool_calls_raw:
                # No more tools called, subagent reached its conclusion
                break

            # Execute subagent tool calls
            formatted_calls = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in tool_calls_raw.values()
            ]
            messages.append(
                ChatMessage(role="assistant", content=assistant_text, tool_calls=formatted_calls)
            )

            for tc in tool_calls_raw.values():
                tool_calls_count += 1
                call_id = tc.get("id") or str(uuid.uuid4().hex[:8])
                tool_name = tc.get("name") or "unknown"
                raw_args = tc.get("arguments") or "{}"
                args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args else {}

                try:
                    tool_output = await self.registry.call_tool(tool_name, args)
                except Exception as err:
                    tool_output = f"Tool execution error: {err}"

                messages.append(
                    ChatMessage(
                        role="tool",
                        content=tool_output,
                        tool_call_id=call_id,
                    )
                )

        duration_ms = max(1, int((time.monotonic() - start_time) * 1000))
        result = SubagentTaskResult(
            role=config.id,
            task=request.task,
            summary=final_summary.strip() or "Task completed with no further comments.",
            status="completed",
            turns_taken=turns_taken,
            tool_calls_count=tool_calls_count,
            duration_ms=duration_ms,
            subagent_id=subagent_id,
        )

        if event_queue:
            await event_queue.put(
                AgentEvent(
                    event_type="subagent_completed",
                    session_id=session_id,
                    payload={
                        "subagent_id": subagent_id,
                        "role": config.id,
                        "task": request.task,
                        "summary": result.summary,
                        "duration_ms": duration_ms,
                        "turns_taken": turns_taken,
                        "tool_calls_count": tool_calls_count,
                    },
                )
            )

        return result
