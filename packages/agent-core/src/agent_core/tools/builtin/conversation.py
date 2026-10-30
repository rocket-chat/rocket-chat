from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Annotated, Literal, NotRequired, TypedDict

from pydantic import Field

from agent_core import plan_review
from agent_core.context import current_execution_ctx
from agent_core.guidance import servers_of_tools
from agent_core.tool_loading import resolve_tool_name
from agent_core.tools.builtin import delegation
from specifications.interfaces.agent import (
    APPROVAL_GRANTED_PREFIX,
    PLAN_APPROVAL_ACTION,
    AgentEvent,
    ServerGuidance,
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


def register(registry: ToolRegistry) -> None:
    _register_discovery_tools(registry)
    _register_dialogue_tools(registry)
    _register_checklist_tools(registry)
    delegation.register(registry)
    _register_planning_tools(registry)


def _register_discovery_tools(registry: ToolRegistry) -> None:
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
            f"- {d.name}{' [load_tools first]' if registry.is_deferred(d.name) else ''}"
            f"{' [read its guidance first]' if registry.guidance_unread(d.name) else ''}: "
            f"{(d.description.strip().splitlines() or [''])[0][:120]}"
            for d in matches[:MAX_TOOLS_LISTED]
        ]
        more = len(matches) - MAX_TOOLS_LISTED
        if more > 0:
            lines.append(f"... and {more} more; narrow the search with a keyword.")
        return "\n".join(lines)

    @registry._tool(
        description=(
            "Load tools listed under 'Tools to load before use' so you can call them: their "
            "definitions are added from your next step and stay for the rest of the session. "
            "Load every tool you expect to need in one call."
        )
    )
    async def load_tools(
        names: Annotated[list[str], Field(description="Exact tool names to load.")],
    ) -> str:
        known = {d.name: d for d in registry.get_tool_definitions()}
        names = [resolve_tool_name(n, known) for n in names]
        loaded = await registry.load(names)
        # Loading a server's tool is the decision to use the server: its guidance comes with it,
        # once, and not again for the next tool of the same server.
        fresh_guidance = await registry.put_guidance_in_effect(
            servers_of_tools(n for n in names if n in known)
        )
        lines = [
            f"- {name}: {(known[name].description.strip().splitlines() or [''])[0][:160]}"
            for name in loaded
            if name in known
        ]
        unknown = [n for n in names if n not in known]
        ready = [n for n in names if n in known and n not in loaded]
        parts = ["Loaded (callable from your next step):\n" + "\n".join(lines)] if lines else []
        if ready:
            parts.append("Already available: " + ", ".join(ready))
        if unknown:
            parts.append(
                "No such tool: " + ", ".join(unknown) + ". Check the names with search_tools."
            )
        if fresh_guidance:
            parts.append(_guidance_given(fresh_guidance))
        return "\n".join(parts) or "Nothing to load."

    @registry._tool(
        description=(
            "Put the usage instructions of an MCP server in effect before you use its tools. "
            "Needed for the servers listed under 'MCP server guidance' in your instructions; "
            "they stay in effect for the whole session, so call it once per server."
        )
    )
    async def load_guidance(
        server: Annotated[
            str, Field(description="The server name exactly as listed, e.g. 'github'.")
        ],
    ) -> str:
        ctx = current_execution_ctx.get()
        guided = sorted(ctx.guidance_by_server) if ctx is not None else []
        wanted = server.strip().lower()
        name = next((g for g in guided if g.lower() == wanted), None)
        if name is None:
            known = ", ".join(guided) or "none"
            return f"No MCP server named '{server}' has usage guidance. Servers that do: {known}."
        fresh = await registry.put_guidance_in_effect([name])
        if not fresh:
            return f"The guidance for '{name}' is already in effect: it is in your instructions."
        return _guidance_given(fresh)


def _guidance_given(entries: list[ServerGuidance]) -> str:
    """What the agent is told once guidance is put in effect.

    The text itself reaches the agent's instructions from its next step (and again on every later
    turn, so it is never lost to pruning); a subagent has no such place and gets it here.
    """
    ctx = current_execution_ctx.get()
    names = ", ".join(f"'{e.server}'" for e in entries)
    if ctx is not None and ctx.guidance_in_result:
        return "\n\n".join(f"Usage guidance for '{e.server}':\n\n{e.text}" for e in entries)
    return (
        f"Usage guidance for {names} is now in effect: it is in your instructions from your next "
        "step and stays there for the whole session. Follow it."
    )


def _register_dialogue_tools(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Ask the person a single- or multi-choice question and pause the turn until they "
            "answer: for an underspecified request, conflicting requirements, or a trade-off that "
            "is theirs (a breaking change, a rename, a destructive action, a blocked "
            "environment). Give distinct options and a recommended default."
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

    # update_status was deprecated: model step text is auto-derived as STATUS_CHANGED.
    # The function is kept for reference but NOT registered.
    async def _deprecated_update_status(
        message: Annotated[
            str,
            Field(
                description="Short human-readable status message describing your current action."
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


def _register_checklist_tools(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Create or update the task checklist the user follows; every call REPLACES the list, "
            "so send all tasks. For work with three or more steps: create it first, keep one task "
            "in_progress, mark each completed as soon as it is done, and before your final answer "
            "leave none pending or in_progress (finished: completed, abandoned: failed). Call it "
            "when a status changes, not on every step."
        )
    )
    async def update_task_checklist(
        tasks: Annotated[list[ChecklistTask], Field(description="List of tasks in the checklist.")],
    ) -> str:
        ctx = current_execution_ctx.get()
        if ctx is not None and ctx.checklist == list(tasks):
            return (
                "Checklist unchanged: the person already sees this exact list. Do not send it "
                "again until a task's status changes; continue with the work."
            )
        if ctx is not None:
            ctx.checklist = [dict(task) for task in tasks]
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
        s_id = ctx.session_id if ctx else registry._session_id
        if registry._driver and s_id:
            try:
                from agent_core.checklist import tasks_to_plan_markdown
                from agent_core.tools import tier2

                plan_md = tasks_to_plan_markdown(tasks)
                await registry._ensure_sandbox(s_id)
                ws_path = registry.workspace_file(".rocket/PLAN.md")
                await tier2.file_write(registry._driver, s_id, ws_path, plan_md, overwrite=True)
            except Exception as e:
                logger.debug("Failed to sync checklist to .rocket/PLAN.md: %s", e)
        return f"Task checklist updated: {completed}/{len(tasks)} tasks completed."


def _register_planning_tools(registry: ToolRegistry) -> None:

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
        approve = registry._turn_callback("approval")
        if not approve:
            return "Blocked: cannot exit plan mode without an approval channel to review the plan."
        critique = ""
        if ctx is not None and ctx.policy.plan_critic:
            critique = await plan_review.critique_plan(registry._subagent_runner, ctx, summary)
        proposed_at = time.time()
        decision = await approve(
            PLAN_APPROVAL_ACTION, plan_review.with_critique(summary, critique), "medium"
        )
        lines = [f"{plan_review.CRITIQUE_HEADING}\n{critique}"] if critique else []
        decided = registry._turn_callback("plan_decision")
        choice = await decided(ctx.session_id, proposed_at) if decided and ctx else None
        if str(decision).startswith(APPROVAL_GRANTED_PREFIX):
            if ctx is not None:
                ctx.plan_mode = False
            lines.extend(plan_review.apply_decision(ctx, choice) if choice else [])
            return "\n".join(
                [f"Plan approved. Plan mode disabled. Proceed with execution: {decision}", *lines]
            )
        lines.extend(plan_review.apply_decision(ctx, choice) if choice else [])
        return "\n".join(
            [f"Plan approval rejected: {decision}. Plan mode remains ACTIVE (read-only).", *lines]
        )

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
