"""A proposed plan: the optional reviewer pass before the person decides, and their decision.

``exit_plan_mode`` raises one approval gate. Around it, this module runs the ``reviewer``
subagent when the organization asked for it (``TurnPolicy.plan_critic``) and turns what the
person chose on the plan card (``PlanDecision``) into the tool's result and the turn's new
permission mode. Everything here reaches the model's context, so every part is bounded.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Protocol

from agent_core.context import ExecutionContext
from specifications.interfaces.agent import (
    MAX_PLAN_FEEDBACK_CHARS,
    MAX_PLAN_TASK_CHARS,
    MAX_PLAN_TASKS,
    PERMISSION_MODES,
    PlanDecision,
    PlanTaskEdit,
    SubagentTaskRequest,
    SubagentTaskResult,
)

logger = logging.getLogger(__name__)

REVIEWER_ROLE = "reviewer"
MAX_CRITIQUE_CHARS = 3_000
CRITIQUE_HEADING = "Reviewer's notes on this plan:"
_CRITIC_TASK = (
    "Review the plan below before a person approves it. Check it against the code: missing "
    "steps, wrong assumptions, risky changes, untested paths. Do not change anything. Answer "
    "with at most eight short points, most important first, or 'No concerns.'"
)


class SubagentRunning(Protocol):
    """The public entry point of the subagent runner this module needs."""

    async def run(
        self,
        request: SubagentTaskRequest,
        session_id: str,
        event_queue: Any = None,
        default_model: str = ...,
    ) -> SubagentTaskResult: ...


def bounded(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters, saying how much was cut."""
    text = text.strip()
    if len(text) <= limit:
        return text
    note = f"… [{len(text) - limit:,} characters cut]"
    return text[: max(0, limit - len(note))] + note


def plan_text(summary: str, checklist: Sequence[dict[str, Any]] | None) -> str:
    """The plan as the reviewer reads it: the summary, then at most ``MAX_PLAN_TASKS`` tasks."""
    lines = [bounded(summary, MAX_PLAN_FEEDBACK_CHARS)]
    tasks = list(checklist or [])
    for task in tasks[:MAX_PLAN_TASKS]:
        lines.append(f"- {bounded(str(task.get('title') or ''), MAX_PLAN_TASK_CHARS)}")
    if len(tasks) > MAX_PLAN_TASKS:
        lines.append(f"(and {len(tasks) - MAX_PLAN_TASKS} more tasks not shown)")
    return "\n".join(lines)


async def critique_plan(
    runner: SubagentRunning | None, ctx: ExecutionContext | None, summary: str
) -> str:
    """The ``reviewer`` subagent's notes on the plan, or ``""`` when none could be had.

    A failed review never blocks the plan: the person still decides, without the notes.
    """
    if runner is None or ctx is None:
        return ""
    request = SubagentTaskRequest(
        role=REVIEWER_ROLE, task=_CRITIC_TASK, context=plan_text(summary, ctx.checklist)
    )
    try:
        result = await runner.run(
            request=request,
            session_id=ctx.session_id,
            event_queue=ctx.event_queue,
            default_model=ctx.parent_model,
        )
    except Exception:
        logger.warning("The plan review by the reviewer subagent failed", exc_info=True)
        return ""
    return bounded(result.summary or "", MAX_CRITIQUE_CHARS)


def with_critique(summary: str, critique: str) -> str:
    """What the plan card shows: the summary, then the reviewer's notes under a heading."""
    return f"{summary}\n\n{CRITIQUE_HEADING}\n{critique}" if critique else summary


def _edited_plan(tasks: Sequence[PlanTaskEdit]) -> list[str]:
    lines = ["The person edited the plan. Follow this checklist (update_task_checklist):"]
    for index, task in enumerate(tasks[:MAX_PLAN_TASKS], start=1):
        detail = f" - {bounded(task.description, MAX_PLAN_TASK_CHARS)}" if task.description else ""
        lines.append(f"{index}. {bounded(task.title, MAX_PLAN_TASK_CHARS)}{detail}")
    return lines


def apply_decision(ctx: ExecutionContext | None, decision: PlanDecision) -> list[str]:
    """Switch the turn to the approved mode; the lines the agent is told about the decision.

    The mode was checked by the platform (lock, ``use_bypass_mode``) before it was stored; an
    unknown value is ignored rather than trusted.
    """
    lines: list[str] = []
    mode = decision.permission_mode
    if decision.approved and ctx is not None and mode in PERMISSION_MODES and mode != "plan":
        ctx.policy = replace(ctx.policy, permission_mode=mode)
        lines.append(f"The rest of the work runs in '{mode}' permission mode.")
    if decision.approved and decision.checklist:
        lines.extend(_edited_plan(decision.checklist))
    if decision.feedback:
        lines.append(f"The person added: {bounded(decision.feedback, MAX_PLAN_FEEDBACK_CHARS)}")
    return lines
