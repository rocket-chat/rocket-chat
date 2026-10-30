"""Delegating to subagents: one task or several, in the foreground or the background, and
bringing a child's branch back into the main tree."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, Field

from agent_core import subagent_worktree as isolation
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.subagent_report import render_for_parent
from agent_core.subagent_roles import EDIT_OR_COMMAND_TOOLS
from specifications.interfaces.agent import (
    APPROVAL_GRANTED_PREFIX,
    ApprovalDetails,
    SubagentConfig,
    SubagentTaskRequest,
)

if TYPE_CHECKING:
    from agent_core.subagents import SubagentRunner
    from agent_core.tools.registry import ToolRegistry

# An implementer's branch merges without a gate in ``auto`` mode (open decision D5): it was
# delegated to write code, and auto mode already lets the agent edit the tree.
AUTO_MERGED_ROLES = frozenset({"implementer"})
MAX_DELEGATIONS = 8


class Delegation(BaseModel):
    role: str = Field(description="The subagent role (see the roles index in your instructions).")
    task: str = Field(description="Clear, specific objective for the subagent.")
    context: str = Field(default="", description="Background hints or files to inspect.")
    skills: list[str] = Field(
        default_factory=list, description="Skills the subagent should follow (by name)."
    )


def _runner(registry: ToolRegistry) -> SubagentRunner | None:
    runner: SubagentRunner | None = registry._subagent_runner
    return runner


def _writes(config: SubagentConfig | None, runner: SubagentRunner) -> bool:
    return config is not None and bool(
        {t.name for t in runner.tools_for(config)} & EDIT_OR_COMMAND_TOOLS
    )


class _Lanes:
    """How many children may run at once: read-only ones freely, isolated writers up to the
    sandbox's capacity, and writers in the shared tree one at a time."""

    def __init__(self, writers: int) -> None:
        self._isolated = asyncio.Semaphore(max(1, writers))
        self._shared = asyncio.Semaphore(1)

    @contextlib.asynccontextmanager
    async def slot(
        self, config: SubagentConfig | None, runner: SubagentRunner
    ) -> AsyncIterator[None]:
        if not _writes(config, runner):
            yield
            return
        lane = self._shared if config is None or config.isolation == "none" else self._isolated
        async with lane:
            yield


async def _delegate(
    runner: SubagentRunner, ctx: ExecutionContext, delegation: Delegation, lanes: _Lanes
) -> str:
    request = SubagentTaskRequest(
        role=delegation.role,
        task=delegation.task,
        context=delegation.context,
        skills=delegation.skills,
    )
    config = runner.roles().get(delegation.role.lower().strip())
    async with lanes.slot(config, runner):
        result = await runner.run(
            request=request,
            session_id=ctx.session_id,
            event_queue=ctx.event_queue,
            default_model=ctx.parent_model,
        )
    return render_for_parent(result)


def register(registry: ToolRegistry) -> None:
    _register_delegate(registry)
    _register_supervision(registry)
    _register_merge(registry)


def _register_delegate(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Delegate one focused task to a subagent role (the roles index in your instructions "
            "says when to use each). The subagent works with its own tools and budget and ends "
            "with a structured report; roles with an isolation work on a branch you merge with "
            "merge_subagent_branch. background=true returns a handle at once."
        )
    )
    async def delegate_subagent(
        role: Annotated[str, Field(description="The subagent role to run.")],
        task: Annotated[str, Field(description="Clear, specific objective for the subagent.")],
        context: Annotated[
            str, Field(description="Optional background hints or files to inspect.")
        ] = "",
        background: Annotated[
            bool,
            Field(
                description=(
                    "Run it in the background and continue; read the result with "
                    "subagent_status or wait_for target=subagent."
                )
            ),
        ] = False,
        skills: Annotated[
            list[str] | None, Field(description="Skills the subagent should follow, by name.")
        ] = None,
    ) -> str:
        ctx = current_execution_ctx.get()
        runner = _runner(registry)
        if runner is None or ctx is None:
            delegate = registry._turn_callback("subagent")
            if delegate:
                return str(await delegate(role, task, context))
            return "Subagent delegation is not available in this session."
        request = SubagentTaskRequest(
            role=role, task=task, context=context, skills=list(skills or []), background=background
        )
        if background:
            return await runner.start_background(request, ctx.session_id, ctx.parent_model)
        result = await runner.run(
            request=request,
            session_id=ctx.session_id,
            event_queue=ctx.event_queue,
            default_model=ctx.parent_model,
        )
        return render_for_parent(result)

    @registry._tool(
        description=(
            "Delegate several independent tasks to subagents at once. Read-only roles run in "
            "parallel; roles that edit or run commands run in parallel only in trees of their "
            "own, as many as the session's sandbox can take, else one after another."
        )
    )
    async def delegate_subagents_parallel(
        delegations: Annotated[
            list[Delegation],
            Field(description=f"The tasks, at most {MAX_DELEGATIONS}.", max_length=MAX_DELEGATIONS),
        ],
    ) -> str:
        ctx = current_execution_ctx.get()
        runner = _runner(registry)
        if not delegations:
            return "No subagent delegations provided."
        if runner is None or ctx is None:
            return "Subagent delegation is not available in this session."
        lanes = _Lanes(ctx.callbacks.max_parallel_writers)
        results = await asyncio.gather(
            *(_delegate(runner, ctx, d, lanes) for d in delegations[:MAX_DELEGATIONS]),
            return_exceptions=True,
        )
        reports = [
            f"[DELEGATION {i + 1} ERROR]: {r}" if isinstance(r, BaseException) else r
            for i, r in enumerate(results)
        ]
        return "\n\n---\n\n".join(reports)


def _register_supervision(registry: ToolRegistry) -> None:
    @registry._tool(
        description="The state of a background subagent, or its report once it has finished."
    )
    async def subagent_status(
        subagent_id: Annotated[str, Field(description="The id delegate_subagent returned.")],
    ) -> str:
        runner = _runner(registry)
        if runner is None:
            return "Subagent delegation is not available in this session."
        return await runner.status(subagent_id.strip())

    @registry._tool(description="Stop a background subagent; its partial transcript is kept.")
    async def subagent_cancel(
        subagent_id: Annotated[str, Field(description="The id delegate_subagent returned.")],
    ) -> str:
        runner = _runner(registry)
        if runner is None:
            return "Subagent delegation is not available in this session."
        return await runner.cancel(subagent_id.strip())


def _register_merge(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Bring a subagent's branch (subagent/<id>) into your working tree: its diff is "
            "shown to the person to approve, then applied as uncommitted changes. Reject it by "
            "not merging."
        )
    )
    async def merge_subagent_branch(
        subagent_id: Annotated[str, Field(description="The subagent whose branch to merge.")],
    ) -> str:
        ctx = current_execution_ctx.get()
        runner = _runner(registry)
        driver = registry._driver
        if ctx is None or runner is None or driver is None:
            return "Merging is not available in this session."
        subagent_id = subagent_id.strip()
        known = runner.branches.get(subagent_id)
        repository = known.repository if known else ctx.active_repository
        if not repository:
            return "No repository is active: set the repository the subagent worked in first."
        diff = await isolation.branch_diff(driver, ctx.session_id, repository, subagent_id)
        if not diff.strip():
            return f"Subagent {subagent_id} has no changes to merge (or no branch in {repository})."
        role = known.role if known else ""
        if not (ctx.policy.permission_mode == "auto" and role in AUTO_MERGED_ROLES):
            if refusal := await _approve_merge(registry, subagent_id, repository, diff):
                return refusal
        applied, output = await isolation.apply_branch(
            driver, ctx.session_id, repository, subagent_id
        )
        if not applied:
            return f"The branch did not apply cleanly to your tree:\n{output}"
        runner.branches.pop(subagent_id, None)
        return (
            f"Merged subagent/{subagent_id} into {repository} as uncommitted changes. Review "
            "them with git_diff before you commit."
        )


async def _approve_merge(
    registry: ToolRegistry, subagent_id: str, repository: str, diff: str
) -> str:
    approve = registry._turn_callback("approval")
    if approve is None:
        return "No approval channel: the merge needs a person to review the diff."
    answer = str(
        await approve(
            "merge_subagent_branch",
            f"Merge the changes of subagent {subagent_id} into {repository}",
            "medium",
            # No ``repository`` slug: a merge is never remembered for a repository.
            ApprovalDetails(tool="merge_subagent_branch", diff=diff),
        )
    )
    if answer.startswith(APPROVAL_GRANTED_PREFIX):
        return ""
    return f"Merge not applied: {answer}"
