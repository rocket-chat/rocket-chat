"""Talking to the person and managing the plan: questions, status, checklist, subagents, plan mode, approvals."""

import logging
from typing import TYPE_CHECKING, Annotated, Literal, NotRequired, TypedDict

from pydantic import Field

from agent_core.context import current_execution_ctx
from specifications.interfaces.agent import (
    AgentEvent,
    SubagentTaskRequest,
)

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ChecklistTask(TypedDict):
    id: Annotated[str, Field(description="Unique task identifier (e.g. '1', 'setup', 'test').")]
    title: Annotated[str, Field(description="Short human-readable title of the task.")]
    status: Annotated[
        Literal["pending", "in_progress", "completed", "failed"],
        Field(description="Current status of the task."),
    ]
    description: NotRequired[
        Annotated[str, Field(description="Optional brief details or context.")]
    ]


MAX_TOOLS_LISTED = 60


def register(registry: "ToolRegistry") -> None:
    _register_discovery_tools(registry)
    _register_dialogue_tools(registry)
    _register_planning_tools(registry)


def _register_discovery_tools(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "List the tools available to you in this session, optionally filtered by a keyword "
            "(matched against names and descriptions). Use it when a call fails with 'no tool "
            "named ...' or when you are unsure what exists, instead of guessing a name."
        )
    )
    async def search_tools(
        query: Annotated[
            str, Field(description="Keyword to look for, e.g. 'github' or 'file'. Empty lists all.")
        ] = "",
    ) -> str:
        needle = query.strip().lower()
        matches = [
            definition
            for definition in sorted(registry.get_tool_definitions(), key=lambda d: d.name)
            if not needle
            or needle in definition.name.lower()
            or needle in definition.description.lower()
        ]
        if not matches:
            return f"No tool matches '{query}'. Call search_tools with no query to list them all."
        lines = [
            f"- {d.name}: {d.description.strip().splitlines()[0][:120]}"
            for d in matches[:MAX_TOOLS_LISTED]
        ]
        more = len(matches) - MAX_TOOLS_LISTED
        if more > 0:
            lines.append(f"... and {more} more; narrow the search with a keyword.")
        return "\n".join(lines)


def _register_dialogue_tools(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Ask the human user a question and WAIT: the whole turn is blocked until they "
            "answer, which may take hours. Use it only as a last resort, when you cannot "
            "continue or choose safely and the answer changes the outcome. Prefer to look "
            "it up (knowledge bases, skills, GitHub tools, the code), or pick the most "
            "reasonable option, say so in your answer and keep going."
        )
    )
    async def ask_question(
        question: Annotated[str, Field(description="The clarification question text.")],
        options: Annotated[
            list[str], Field(description="List of selectable options for the user.")
        ],
        is_multi_select: Annotated[
            bool, Field(description="Whether multiple options can be chosen (default: false).")
        ] = False,
        default_recommended_option: Annotated[
            str | None, Field(description="Recommended default choice.")
        ] = None,
    ) -> str:
        ask = registry._turn_callback("question")
        if ask:
            return str(await ask(question, options, is_multi_select, default_recommended_option))
        return f"Question submitted: {question} (Options: {options})"

    @registry._tool(
        description=(
            "Rename the current session with a concise, descriptive title (3-6 words) "
            "reflecting the topic."
        )
    )
    async def session_rename(
        title: Annotated[
            str, Field(description="The new concise, descriptive title for the session.")
        ],
    ) -> str:
        ctx = current_execution_ctx.get()
        target_session_id = ctx.session_id if ctx else registry._session_id
        rename = registry._turn_callback("rename")
        if rename and target_session_id:
            await rename(target_session_id, title)
            return f"Session successfully renamed to: {title}"
        return f"Session title update requested: {title}"

    @registry._tool(
        description=(
            "Update the real-time status indicator displayed to the user in the Cockpit"
            " or Slack loader. MANDATORY PROTOCOL: You MUST call this before executing "
            "any actionable task, tool sequence, investigation, or code modification to"
            " keep the user informed in real time."
        )
    )
    async def update_status(
        message: Annotated[
            str,
            Field(
                description="Short human-readable status message describing your current action in present-continuous tense."
            ),
        ],
    ) -> str:
        ctx = current_execution_ctx.get()
        if ctx and ctx.event_queue:
            ctx.event_queue.put_nowait(
                AgentEvent(
                    event_type="status",
                    session_id=ctx.session_id,
                    payload={"status": "running", "message": message},
                )
            )
        return f"Status updated: {message}"

    @registry._tool(
        description=(
            "Record an explicit reasoning checkpoint (scratchpad) before the next "
            "action. The checkpoint is surfaced to the user as a reasoning event."
        )
    )
    async def think(
        thought: Annotated[str, Field(description="The reasoning checkpoint to record.")],
    ) -> str:
        ctx = current_execution_ctx.get()
        if ctx and ctx.event_queue:
            ctx.event_queue.put_nowait(
                AgentEvent(
                    event_type="thought",
                    session_id=ctx.session_id,
                    payload={"content": thought},
                )
            )
        return "Reasoning checkpoint recorded."


def _register_planning_tools(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Create or update the task checklist the user follows. Every call REPLACES the whole "
            "list, so always send all tasks. Use it for work with three or more steps: create it "
            "first, keep exactly one task in_progress, and mark each task completed as soon as "
            "it is done, not later. Before your final answer no task may stay pending or "
            "in_progress: mark finished work completed and abandoned work failed. Send an empty "
            "list to clear it when it no longer helps."
        )
    )
    async def update_task_checklist(
        tasks: Annotated[list[ChecklistTask], Field(description="List of tasks in the checklist.")],
    ) -> str:
        ctx = current_execution_ctx.get()
        if ctx and ctx.event_queue:
            ctx.event_queue.put_nowait(
                AgentEvent(
                    event_type="plan_updated",
                    session_id=ctx.session_id,
                    payload={"session_id": ctx.session_id, "tasks": tasks},
                )
            )
        on_plan = registry._turn_callback("plan")
        if on_plan:
            on_plan(tasks)
        completed = sum(1 for t in tasks if t.get("status") == "completed")
        return f"Task checklist updated: {completed}/{len(tasks)} tasks completed."

    @registry._tool(
        description=(
            "Delegate an isolated investigation or task to a specialist subagent (e.g. "
            "security_auditor, qa_verifier, researcher)."
        )
    )
    async def delegate_subagent(
        role: Annotated[
            str,
            Field(
                description="Specialist role to spawn: 'security_auditor', 'qa_verifier', or 'researcher'."
            ),
        ],
        task: Annotated[
            str, Field(description="Clear, specific objective for the subagent to accomplish.")
        ],
        context: Annotated[
            str, Field(description="Optional background hints or specific files to inspect.")
        ] = "",
    ) -> str:
        ctx = current_execution_ctx.get()
        if registry._subagent_runner and ctx:
            sub_req = SubagentTaskRequest(role=role, task=task, context=context)
            sub_res = await registry._subagent_runner.run(
                request=sub_req,
                session_id=ctx.session_id,
                event_queue=ctx.event_queue,
                default_model=ctx.parent_model,
            )
            return (
                f"[{sub_res.role.upper()} SUBAGENT REPORT]\n"
                f"Task: {sub_res.task}\n"
                f"Status: {sub_res.status} ({sub_res.turns_taken} turns, {sub_res.tool_calls_count} tools, {sub_res.duration_ms}ms)\n\n"
                f"{sub_res.summary}"
            )
        delegate = registry._turn_callback("subagent")
        if delegate:
            return str(await delegate(role, task, context))
        return f"Subagent delegation requested for role '{role}': {task}"

    @registry._tool(
        description=(
            "Enter read-only plan mode. Use for multi-step or risky work: investigate "
            "safely, then present a plan before changing anything."
        )
    )
    async def enter_plan_mode() -> str:
        ctx = current_execution_ctx.get()
        if ctx is not None:
            ctx.plan_mode = True
        return (
            "Plan mode is now ACTIVE (read-only). Investigate with read-only tools, "
            "record the plan via update_task_checklist, then call exit_plan_mode to "
            "request human approval before making any changes."
        )

    @registry._tool(description="Leave plan mode and request human approval for the proposed plan.")
    async def exit_plan_mode(
        summary: Annotated[
            str, Field(description="Concise summary of the plan requiring approval.")
        ],
    ) -> str:
        ctx = current_execution_ctx.get()
        if ctx is not None:
            ctx.plan_mode = False
        approve = registry._turn_callback("approval")
        if approve:
            decision = await approve("execute_plan", summary, "medium")
            return f"Plan approval result: {decision}"
        return "Plan mode disabled. Proceed with the plan."

    @registry._tool(
        description=(
            "Ask the human to approve a sensitive or destructive action (force push, "
            "hard reset, data deletion, costly external call) before performing it."
        )
    )
    async def request_approval(
        action_type: Annotated[
            str, Field(description="Short action identifier, e.g. 'git_revert_hard'.")
        ],
        description: Annotated[str, Field(description="What will happen and why it is needed.")],
        risk_level: Annotated[
            Literal["low", "medium", "high", "critical"],
            Field(description="Assessed risk level (default: medium)."),
        ] = "medium",
    ) -> str:
        approve = registry._turn_callback("approval")
        if not approve:
            return (
                "No approval channel is configured. Explain the action clearly to the "
                "user and proceed only if it is safe and reversible."
            )
        return str(await approve(action_type, description, risk_level))
