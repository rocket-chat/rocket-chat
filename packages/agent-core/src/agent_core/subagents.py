"""Subagents: a delegated task run as a bounded ReAct loop of its own.

A child has its role's prompt and tools, what it inherits from the parent (each part clamped,
see ``subagent_context``), its own steps, dollars and wall-clock budget, and, for a role with an
isolation, a tree of its own whose changes come back as a branch (``subagent_worktree``). It
ends with a structured ``report`` (``subagent_report``). A foreground child runs inside the
parent's tool call; a background one is a supervised task the parent checks on later.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import json
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from llm_gateway.pricing import estimate_cost_usd
from llm_gateway.reasoning import merge_reasoning_blocks
from llm_gateway.tokens import estimated_usage

from agent_core import subagent_worktree as isolation
from agent_core.context import ContextManager, ExecutionContext, current_execution_ctx
from agent_core.dispatcher import ToolDispatcher
from agent_core.leaked_calls import (
    LEAK_NOTICE,
    MAX_LEAK_RETRIES,
    contains_leaked_calls,
    strip_leaked_calls,
)
from agent_core.progress import ProgressTracker
from agent_core.repeats import repeat_note
from agent_core.retry_policy import execute_stream_with_retry
from agent_core.subagent_context import (
    child_callbacks,
    inherited_sections,
    report_tool_for,
    workspace_brief,
)
from agent_core.subagent_events import SubagentEvents
from agent_core.subagent_report import (
    parse_report,
    render_for_parent,
    report_to_dict,
    schema_problem,
)
from agent_core.subagent_roles import (
    DEFAULT_SUBAGENT_CONFIGS,
    EDIT_OR_COMMAND_TOOLS,
    EDIT_TOOLS,
    merge_repository_roles,
)
from agent_core.subagent_scope import SubagentFrame, current_tool_call_id
from agent_core.summarizer import model_summarizer
from agent_core.tools.clamp import clamp_output
from agent_core.tools.offload import offload_tool_output
from agent_core.tools.registry import ToolRegistry
from agent_core.truncation import (
    MAX_EMPTY_RETRIES,
    empty_reply_give_up,
    empty_reply_notice,
    split_truncated,
    truncation_notice,
)
from specifications.interfaces.agent import (
    NO_STEP_LIMIT,
    AgentEvent,
    SubagentChanges,
    SubagentConfig,
    SubagentReport,
    SubagentRunRecord,
    SubagentTaskRequest,
    SubagentTaskResult,
    SubagentTranscriptRecord,
)
from specifications.interfaces.llm import (
    CONTEXT_WINDOW_EXCEEDED,
    ChatMessage,
    LLMGatewayProtocol,
    ModelCatalogueProtocol,
    ModelExecutionError,
    ModelRequest,
    ToolDefinition,
    current_model_scope,
)

__all__ = ["DEFAULT_SUBAGENT_CONFIGS", "SubagentRunner", "result_from_dict", "result_to_dict"]

logger = logging.getLogger(__name__)

# Tools a child never gets: it may not delegate, steer the parent's plan, merge branches, or
# register a wait that would pause the parent's session.
NOT_FOR_CHILDREN = frozenset(
    {
        "delegate_subagent",
        "delegate_subagents_parallel",
        "merge_subagent_branch",
        "subagent_status",
        "subagent_cancel",
        "update_task_checklist",
        "enter_plan_mode",
        "exit_plan_mode",
        "wait_for",
    }
)
NO_DELEGATION = NOT_FOR_CHILDREN
FINAL_REPORT_NOTE = (
    "You have reached your limit ({reason}). Do not call tools: report now what you found, "
    "what you could not check, and what the main agent should do next."
)
REPORT_NUDGE = "Call `report` now with your result: the main agent reads only that."
# Errors the runner swallowed so the delegation could finish, by stage (logged each time).
SUBAGENT_ERRORS: collections.Counter[str] = collections.Counter()
# How long a cancelled child's clean-up (its worktree, its sandbox) may take.
CLEANUP_TIMEOUT_SECONDS = 60
CANCEL_WAIT_SECONDS = 10


def _count_error(stage: str, subagent_id: str) -> None:
    SUBAGENT_ERRORS[stage] += 1
    logger.warning("Subagent %s: %s failed", subagent_id, stage, exc_info=True)


def result_to_dict(result: SubagentTaskResult) -> dict[str, Any]:
    return dataclasses.asdict(result)


def result_from_dict(raw: Mapping[str, Any]) -> SubagentTaskResult:
    """A result stored as plain data (a background run's), with its report rebuilt."""
    from agent_core.subagent_report import report_from_dict

    names = {f.name for f in dataclasses.fields(SubagentTaskResult)}
    values = {k: v for k, v in raw.items() if k in names and k != "report"}
    result = SubagentTaskResult(**values)
    result.report = report_from_dict(raw.get("report"))
    return result


def unknown_role(
    role: str, roles: Mapping[str, SubagentConfig], subagent_id: str
) -> SubagentTaskResult:
    known = ", ".join(sorted(r.id for r in roles.values() if r.enabled)) or "none"
    return SubagentTaskResult(
        role=role,
        task="",
        summary=f"There is no '{role}' subagent role. Roles you can delegate to: {known}.",
        status="failed",
        subagent_id=subagent_id,
    )


@dataclass
class _Generation:
    """What one model call produced."""

    text: str = ""
    reasoning: str = ""
    reasoning_blocks: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    usage: dict[str, int] | None = None
    cost_usd: float | None = None
    finish_reason: str | None = None

    def produced_text(self) -> str:
        return (
            self.text
            + self.reasoning
            + "".join(call["arguments"] for call in self.tool_calls.values())
        )

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


@dataclass
class _BranchInfo:
    """Where a child's branch lives, for ``merge_subagent_branch``."""

    repository: str
    role: str


class SubagentRunner:
    """Runs delegated tasks; keeps the background ones and the branches they left."""

    def __init__(
        self,
        gateway: LLMGatewayProtocol,
        registry: ToolRegistry,
        configs: dict[str, SubagentConfig] | None = None,
        dispatcher: ToolDispatcher | None = None,
        catalogue: ModelCatalogueProtocol | None = None,
    ) -> None:
        self.gateway = gateway
        self.registry = registry
        # Shared with the main turn, so a delegate's calls count against the same limits.
        self.dispatcher = dispatcher or ToolDispatcher(registry=registry)
        self.configs: dict[str, SubagentConfig] = configs or dict(DEFAULT_SUBAGENT_CONFIGS)
        self.context_manager = ContextManager(catalogue=catalogue)
        self._background: dict[str, asyncio.Task[SubagentTaskResult]] = {}
        self._finished: dict[str, SubagentTaskResult] = {}
        self.branches: dict[str, _BranchInfo] = {}

    def roles(self) -> Mapping[str, SubagentConfig]:
        """The organization's roles for this turn (else the runner's own), plus the roles the
        active repository defines that no organization role takes."""
        ctx = current_execution_ctx.get()
        configured: Mapping[str, SubagentConfig] = self.configs
        if ctx is not None and ctx.callbacks.subagent_configs is not None:
            configured = ctx.callbacks.subagent_configs
        if ctx is not None and ctx.repository_subagents:
            return merge_repository_roles(configured, ctx.repository_subagents)
        return configured

    def tools_for(self, config: SubagentConfig) -> list[ToolDefinition]:
        """The tool definitions a child may use (never the delegation or session tools)."""
        ctx = current_execution_ctx.get()
        denied = ctx.denied_tools if ctx is not None else frozenset()
        refused = set(config.disallowed_tools) | NOT_FOR_CHILDREN
        all_tools = [
            t
            for t in self.registry.get_tool_definitions()
            if t.name not in denied and t.name not in refused
        ]
        if "*" in config.whitelisted_tools:
            return all_tools
        allowed = set(config.whitelisted_tools)
        return [tool for tool in all_tools if tool.name in allowed]

    def model_for(self, config: SubagentConfig, default_model: str) -> str:
        ctx = current_execution_ctx.get()
        resolve = ctx.callbacks.subagent_model if ctx is not None else None
        if resolve is not None:
            try:
                if model := resolve(config):
                    return model
            except Exception:
                _count_error("model resolution", config.id)
        return config.model or default_model

    def _resolve(
        self, request: SubagentTaskRequest, subagent_id: str
    ) -> SubagentConfig | SubagentTaskResult:
        roles = self.roles()
        config = roles.get(request.role.lower().strip())
        if config is None:
            return unknown_role(request.role, roles, subagent_id)
        if not config.enabled:
            return SubagentTaskResult(
                role=config.id,
                task=request.task,
                summary=f"The '{config.id}' subagent is disabled by the organization.",
                status="failed",
                subagent_id=subagent_id,
            )
        return config

    async def run(
        self,
        request: SubagentTaskRequest,
        session_id: str,
        event_queue: asyncio.Queue[AgentEvent | None] | None = None,
        default_model: str = "",
    ) -> SubagentTaskResult:
        """Run one child to the end, inside the caller's task."""
        subagent_id = request.subagent_id or f"sub_{uuid.uuid4().hex[:8]}"
        resolved = self._resolve(request, subagent_id)
        if isinstance(resolved, SubagentTaskResult):
            return resolved
        ctx = current_execution_ctx.get()
        events = SubagentEvents(
            session_id=session_id,
            subagent_id=subagent_id,
            parent_tool_call_id=request.parent_tool_call_id or current_tool_call_id.get(),
            queue=event_queue,
            sink=ctx.callbacks.subagent_event_sink if (ctx and request.background) else None,
        )
        execution = _SubagentExecution(
            runner=self,
            request=request,
            session_id=session_id,
            config=resolved,
            subagent_id=subagent_id,
            model=self.model_for(resolved, default_model or (ctx.parent_model if ctx else "")),
            events=events,
        )
        return await execution.execute()

    # --- background children ------------------------------------------------------------

    async def start_background(
        self, request: SubagentTaskRequest, session_id: str, default_model: str = ""
    ) -> str:
        """Start a child as a supervised task and return what the parent is told at once."""
        ctx = current_execution_ctx.get()
        runs = ctx.callbacks.subagent_runs if ctx is not None else None
        if ctx is None or runs is None:
            return "Background delegation is not available here: delegate in the foreground."
        subagent_id = request.subagent_id or f"sub_{uuid.uuid4().hex[:8]}"
        resolved = self._resolve(request, subagent_id)
        if isinstance(resolved, SubagentTaskResult):
            return render_for_parent(resolved)
        request = dataclasses.replace(
            request,
            subagent_id=subagent_id,
            background=True,
            parent_tool_call_id=request.parent_tool_call_id or current_tool_call_id.get(),
        )
        now = int(time.time())
        record = SubagentRunRecord(
            id=f"run_{uuid.uuid4().hex[:16]}",
            org_id=ctx.tenant_org_id,
            session_id=session_id,
            subagent_id=subagent_id,
            role=resolved.id,
            task=request.task,
            context=request.context,
            status="running",
            background=True,
            parent_tool_call_id=request.parent_tool_call_id,
            created_at=now,
            updated_at=now,
        )
        await runs.save_run(record)
        task = asyncio.create_task(self._run_background(record, request, session_id, default_model))
        self._background[subagent_id] = task
        return (
            f"Started {resolved.id} subagent {subagent_id} in the background. Check it with "
            f"`subagent_status` (subagent_id {subagent_id}) or `wait_for` target=subagent; "
            "it reports when it finishes."
        )

    async def _run_background(
        self,
        record: SubagentRunRecord,
        request: SubagentTaskRequest,
        session_id: str,
        default_model: str,
    ) -> SubagentTaskResult:
        ctx = current_execution_ctx.get()
        runs = ctx.callbacks.subagent_runs if ctx is not None else None
        status, result = "failed", None
        try:
            result = await self.run(request, session_id, None, default_model)
            status = result.status
            return result
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        finally:
            self._background.pop(record.subagent_id, None)
            if result is not None:
                self._finished[record.subagent_id] = result
            if runs is not None:
                finished = dataclasses.replace(
                    record,
                    status=status,
                    result=result_to_dict(result) if result is not None else None,
                    cost_usd=result.cost if result is not None else 0.0,
                    updated_at=int(time.time()),
                )
                try:
                    await asyncio.shield(runs.save_run(finished))
                except Exception:
                    _count_error("saving the background run", record.subagent_id)

    async def settled(self, subagent_id: str) -> SubagentTaskResult | None:
        """The finished result of a child, or ``None`` while it still runs (or is unknown)."""
        if subagent_id in self._background:
            return None
        if subagent_id in self._finished:
            return self._finished[subagent_id]
        stored = await self._stored_run(subagent_id)
        if stored is None or stored.status in ("queued", "running") or stored.result is None:
            return None
        return result_from_dict(stored.result)

    async def _stored_run(self, subagent_id: str) -> SubagentRunRecord | None:
        ctx = current_execution_ctx.get()
        runs = ctx.callbacks.subagent_runs if ctx is not None else None
        if ctx is None or runs is None:
            return None
        try:
            return await runs.get_run(ctx.tenant_org_id, ctx.session_id, subagent_id)
        except Exception:
            _count_error("reading the background run", subagent_id)
            return None

    async def status(self, subagent_id: str) -> str:
        if subagent_id in self._background:
            return f"Subagent {subagent_id} is still running."
        if (result := await self.settled(subagent_id)) is not None:
            return render_for_parent(result)
        stored = await self._stored_run(subagent_id)
        if stored is None:
            return f"There is no subagent {subagent_id} in this session."
        return f"Subagent {subagent_id} ({stored.role}) is {stored.status}."

    async def cancel(self, subagent_id: str) -> str:
        task = self._background.get(subagent_id)
        if task is not None:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=CANCEL_WAIT_SECONDS)
            except (asyncio.CancelledError, TimeoutError):
                pass
            except Exception:
                _count_error("cancelling", subagent_id)
            return f"Cancelled subagent {subagent_id}; its partial transcript is saved."
        stored = await self._stored_run(subagent_id)
        if stored is None:
            return f"There is no subagent {subagent_id} in this session."
        if stored.status not in ("queued", "running"):
            return f"Subagent {subagent_id} already ended ({stored.status})."
        ctx = current_execution_ctx.get()
        if ctx is not None and ctx.callbacks.subagent_runs is not None:
            # Another replica runs it: it reads its run between steps and stops.
            await ctx.callbacks.subagent_runs.save_run(
                dataclasses.replace(stored, status="cancelled", updated_at=int(time.time()))
            )
        return f"Asked subagent {subagent_id} to stop; it stops at its next step."

    def remember_branch(self, subagent_id: str, repository: str, role: str) -> None:
        self.branches[subagent_id] = _BranchInfo(repository=repository, role=role)


class _SubagentExecution:
    """One delegated task: its private conversation, its tree, its budget and its counters."""

    def __init__(
        self,
        *,
        runner: SubagentRunner,
        request: SubagentTaskRequest,
        session_id: str,
        config: SubagentConfig,
        subagent_id: str,
        model: str,
        events: SubagentEvents,
    ) -> None:
        self._runner = runner
        self._request = request
        self._session_id = session_id
        # The sandbox the child's tools act on: its own for ``sandbox`` isolation.
        self._sandbox_id = session_id
        self._config = config
        self._subagent_id = subagent_id
        self._model = model
        self._events = events
        self._tools = runner.tools_for(config)
        self._report_tool = report_tool_for(config)
        self._permitted = {tool.name for tool in self._tools}
        self._is_writer = bool(self._permitted & EDIT_OR_COMMAND_TOOLS)
        self._messages: list[ChatMessage] = []
        # Everything said, whole: the live conversation is pruned and compacted, this is not.
        self._record: list[ChatMessage] = []
        self._turns_taken = 0
        self._last_compaction_step: int | None = None
        self._tool_calls_count = 0
        self._empty_retries = 0
        self._leaked_retries = 0
        self._nudged_for_report = False
        self._progress_tracker = ProgressTracker()
        self._status = "completed"
        self._stop_reason = ""
        self._final_summary = ""
        self._report: SubagentReport | None = None
        self._tokens = 0
        self._cost = 0.0
        self._started = time.monotonic()
        self._edited: list[str] = []
        self._dirty_before: set[str] = set()
        self._worktree: isolation.Worktree | None = None
        self._repository = ""
        self._branch: str | None = None
        self._changes: SubagentChanges | None = None
        self._changed: list[str] = []
        self._forward_queue: asyncio.Queue[AgentEvent | None] | None = None
        self._forwarder: asyncio.Task[None] | None = None

    # --- lifecycle -------------------------------------------------------------------------

    async def execute(self) -> SubagentTaskResult:
        parent = current_execution_ctx.get()
        await self._announce_started()
        try:
            child_ctx = await self._prepare(parent)
        except isolation.IsolationError as err:
            self._status, self._final_summary = (
                "failed",
                (f"The subagent did not run: {err}. Nothing was written to your tree."),
            )
            return await self._finish(cleanup=False)
        ctx_token = current_execution_ctx.set(child_ctx) if child_ctx is not None else None
        scope_token = self._enter_model_scope()
        try:
            await self._run_with_wall_clock()
        except asyncio.CancelledError:
            self._status = "cancelled"
            self._final_summary = self._final_summary or "Cancelled before it finished."
            # The clean-up runs outside the cancelled task, so the cancellation cannot cut it.
            await asyncio.shield(asyncio.ensure_future(self._finish()))
            raise
        finally:
            if scope_token is not None:
                current_model_scope.reset(scope_token)
            if ctx_token is not None:
                current_execution_ctx.reset(ctx_token)
        return await self._finish()

    def _enter_model_scope(self) -> Any:
        scope = current_model_scope.get()
        if scope is None:
            return None
        return current_model_scope.set(
            dataclasses.replace(scope, subagent_id=self._subagent_id, call_role="subagent")
        )

    async def _run_with_wall_clock(self) -> None:
        limit = self._config.max_wall_seconds
        if limit <= 0:
            await self._run_turns()
            return
        try:
            async with asyncio.timeout(limit):
                await self._run_turns()
        except TimeoutError:
            self._stop_reason = f"{limit} s of wall-clock time"
            await self._final_report()

    async def _prepare(self, parent: ExecutionContext | None) -> ExecutionContext | None:
        """The child's tree (failing rather than writing to the parent's), context and prompt."""
        if parent is not None:
            await self._isolate(parent)
        frame = SubagentFrame(
            subagent_id=self._subagent_id,
            role=self._config.id,
            # A background child may outlive the turn whose gates would reach the person.
            can_ask_user=self._config.can_ask_user and not self._request.background,
            parent_tool_call_id=self._events.parent_tool_call_id,
        )
        sections = await inherited_sections(
            self._config, self._request, parent, self._runner.registry._driver, self._session_id
        )
        active = (
            self._worktree.directory
            if self._worktree
            else (parent.active_repository if parent else "")
        )
        brief = workspace_brief(self._permitted, active, self._isolated)
        system = "\n\n".join([self._config.system_prompt, brief, *sections])
        context = f"\nContext: {self._request.context}" if self._request.context else ""
        self._say(ChatMessage(role="system", content=system))
        self._say(
            ChatMessage(role="user", content=f"Delegated task: {self._request.task}{context}")
        )
        if parent is None:
            return None
        return dataclasses.replace(
            parent,
            session_id=self._sandbox_id,
            active_repository=active,
            guidance_in_effect={},
            guidance_pending=[],
            guidance_in_result=True,
            skills_in_effect={},
            skill_texts={},
            standing_pending=[],
            subagent=frame,
            callbacks=child_callbacks(parent.callbacks, frame, isolated=self._isolated),
            event_queue=self._child_queue(parent),
        )

    def _child_queue(self, parent: ExecutionContext) -> Any:
        """Where the child's tools put their own events (a command's output): the parent's queue,
        or for a background child, which may outlive it, a queue forwarded to the session."""
        sink = parent.callbacks.subagent_event_sink
        if not self._request.background or sink is None:
            return parent.event_queue
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

        async def forward() -> None:
            while (event := await queue.get()) is not None:
                if event.event_type != "model_usage":
                    try:
                        await sink(event)
                    except Exception:
                        _count_error("forwarding an event", self._subagent_id)

        self._forwarder = asyncio.create_task(forward())
        self._forward_queue = queue
        return queue

    @property
    def _isolated(self) -> bool:
        return self._worktree is not None or self._sandbox_id != self._session_id

    async def _isolate(self, parent: ExecutionContext) -> None:
        mode = self._config.isolation
        driver = self._runner.registry._driver
        self._repository = parent.active_repository or ""
        if mode == "none" or driver is None:
            if self._is_writer and driver is not None and self._repository:
                self._dirty_before = set(
                    await isolation.uncommitted_files(driver, self._session_id, self._repository)
                )
            return
        if not self._repository:
            raise isolation.IsolationError(
                f"the '{self._config.id}' role works in a tree of its own, which needs an active "
                "repository; choose one (set_active_repository) first"
            )
        if mode == "sandbox":
            await self._isolate_in_sandbox(parent)
            return
        self._worktree = await isolation.setup_worktree(
            driver, self._session_id, self._repository, self._subagent_id
        )

    async def _isolate_in_sandbox(self, parent: ExecutionContext) -> None:
        driver = self._runner.registry._driver
        spec_of = parent.callbacks.subagent_sandbox_spec
        if driver is None or spec_of is None:
            raise isolation.IsolationError("this deployment cannot start a sandbox for a subagent")
        child_id = isolation.child_sandbox_id(self._session_id, self._subagent_id)
        try:
            spec = await spec_of(child_id)
        except Exception as err:
            raise isolation.IsolationError(f"no workspace could be prepared for it: {err}") from err
        await isolation.provision_child_sandbox(driver, self._session_id, child_id, spec)
        self._sandbox_id = child_id
        await isolation.start_child_branch(driver, child_id, self._repository, self._subagent_id)

    async def _collect_changes(self) -> None:
        """What the child changed, from git in its tree (shell edits count as much as tool edits)."""
        driver = self._runner.registry._driver
        if driver is None or not self._repository:
            self._changed = list(self._edited)
            return
        if self._worktree is not None:
            await isolation.commit_worktree(
                driver, self._session_id, self._worktree, self._config.id
            )
        elif self._sandbox_id != self._session_id:
            await isolation.bring_back_branch(
                driver, self._session_id, self._sandbox_id, self._repository,
                self._subagent_id, self._config.id,
            )  # fmt: skip
        else:
            after = await isolation.uncommitted_files(driver, self._session_id, self._repository)
            fresh = [f for f in after if f not in self._dirty_before]
            self._changed = list(dict.fromkeys([*fresh, *self._edited]))
            return
        stat, files = await isolation.changed_files(
            driver, self._session_id, self._repository, self._subagent_id
        )
        self._branch = isolation.branch_of(self._subagent_id) if files else None
        self._changed = files
        self._changes = SubagentChanges(branch=self._branch, diff_stat=stat, files=files)
        if self._branch:
            self._runner.remember_branch(self._subagent_id, self._repository, self._config.id)

    async def _cleanup(self) -> None:
        driver = self._runner.registry._driver
        if driver is None:
            return
        if self._worktree is not None:
            await isolation.remove_worktree(driver, self._session_id, self._worktree)
        if self._sandbox_id != self._session_id:
            await driver.destroy_workspace(self._sandbox_id)

    async def _finish(self, *, cleanup: bool = True) -> SubagentTaskResult:
        self._changed = list(self._edited)
        if cleanup:
            try:
                async with asyncio.timeout(CLEANUP_TIMEOUT_SECONDS):
                    await self._collect_changes()
            except Exception:
                _count_error("collecting changes", self._subagent_id)
            try:
                async with asyncio.timeout(CLEANUP_TIMEOUT_SECONDS):
                    await self._cleanup()
            except Exception:
                _count_error("cleaning up its tree", self._subagent_id)
        await self._events.flush()
        if self._forward_queue is not None:
            await self._forward_queue.put(None)
        result = self._result()
        await self._announce_completed(result)
        await self._persist_transcript(result)
        return result

    # --- the loop ----------------------------------------------------------------------------

    async def _run_turns(self) -> None:
        while not self._report:
            if not self._has_steps_left():
                self._stop_reason = f"{self._config.max_turns} steps"
                await self._final_report()
                return
            if reason := await self._over_budget():
                self._stop_reason = reason
                await self._final_report()
                return
            self._turns_taken += 1
            await self._keep_context_small()
            generation = await self._generate_with_recovery()
            if generation is None:
                return
            if generation.text:
                self._final_summary = generation.text
                self._empty_retries = 0
            if self._handle_step_tool_calls(generation):
                continue
            if not generation.tool_calls:
                if self._should_continue_without_tools(generation):
                    continue
                return
            await self._run_tool_calls(generation)

    def _should_continue_without_tools(self, generation: _Generation) -> bool:
        """A reply with no tool call: retry an empty one, ask once for the report, else stop."""
        if not generation.text and self._nudged_for_report:
            return False  # its answer before the nudge stands
        if not generation.text:
            if self._empty_retries < MAX_EMPTY_RETRIES:
                self._empty_retries += 1
                notice = empty_reply_notice(
                    generation.finish_reason, remaining_steps=self._steps_left()
                )
                self._say(ChatMessage(role="user", content=notice))
                return True
            self._status = "partial"
            self._final_summary = empty_reply_give_up(generation.finish_reason)
            return False
        if not self._nudged_for_report and self._has_steps_left():
            self._nudged_for_report = True
            self._say(ChatMessage(role="assistant", content=generation.text))
            self._say(ChatMessage(role="user", content=REPORT_NUDGE))
            return True
        return False

    async def _over_budget(self) -> str:
        """Why the child must stop and report now (its dollars, the turn's, a cancellation)."""
        cap = self._config.max_cost_usd
        if cap > 0 and self._cost >= cap:
            return f"its ${cap:.2f} budget"
        ctx = current_execution_ctx.get()
        turn_cap = ctx.policy.max_turn_cost_usd if ctx is not None else None
        if ctx is not None and turn_cap and ctx.turn_spend.total_usd >= turn_cap:
            return f"the turn's ${turn_cap:.2f} budget"
        if self._request.background and await self._cancel_requested():
            self._status = "cancelled"
            return "a cancellation"
        return ""

    async def _cancel_requested(self) -> bool:
        stored = await self._runner._stored_run(self._subagent_id)
        return stored is not None and stored.status == "cancelled"

    def _handle_step_tool_calls(self, generation: _Generation) -> bool:
        """Process truncation/leaked calls and check if tools should run; True if retry step."""
        generation.tool_calls, cut = split_truncated(
            generation.tool_calls, generation.finish_reason
        )
        if generation.tool_calls and contains_leaked_calls(generation.text):
            generation.text = strip_leaked_calls(generation.text)
            self._final_summary = generation.text
        if cut:
            self._say(ChatMessage(role="user", content=truncation_notice(cut)))
            if not generation.tool_calls:
                return True
        if not generation.tool_calls and contains_leaked_calls(generation.text):
            self._final_summary = strip_leaked_calls(generation.text)
            if self._leaked_retries < MAX_LEAK_RETRIES:
                self._leaked_retries += 1
                self._say(ChatMessage(role="user", content=LEAK_NOTICE))
                return True
        return False

    async def _generate_with_recovery(self) -> _Generation | None:
        try:
            return await self._generate()
        except ModelExecutionError as err:
            if getattr(err, "finish_reason", None) != CONTEXT_WINDOW_EXCEEDED:
                self._status = "failed"
                self._final_summary = f"Subagent error during generation: {err}"
                return None
        except Exception as err:
            self._status = "failed"
            self._final_summary = f"Subagent error during generation: {err}"
            return None
        await self._keep_context_small(force_compaction=True)
        try:
            return await self._generate()
        except Exception as err:
            self._status = "failed"
            self._final_summary = f"Subagent error during generation: {err}"
            return None

    def _say(self, message: ChatMessage) -> None:
        self._messages.append(message)
        self._record.append(message)

    def _has_steps_left(self) -> bool:
        limit = self._config.max_turns
        return limit == NO_STEP_LIMIT or self._turns_taken < limit

    def _steps_left(self) -> int | None:
        limit = self._config.max_turns
        return None if limit == NO_STEP_LIMIT else max(0, limit - self._turns_taken)

    async def _keep_context_small(self, force_compaction: bool = False) -> None:
        """Prune and compact the delegate's conversation like the main agent's.

        A delegate that works for dozens of steps would otherwise overflow its window; what is
        compacted away stays in the saved transcript.
        """
        ctx = current_execution_ctx.get()
        generation = ctx.policy.generation if ctx is not None else None
        tools = [*self._tools, self._report_tool]
        result = await self._runner.context_manager.enforce_hygiene_async(
            self._model,
            self._messages,
            summarize=model_summarizer(self._runner.gateway, self._model, tools),
            tools=tools,
            overrides=generation.context_windows if generation else (),
            target_tokens=generation.context_target_tokens if generation else 0,
            step=self._turns_taken,
            last_compaction_step=self._last_compaction_step,
            force_compaction=force_compaction,
        )
        if getattr(result, "compacted", False):
            self._last_compaction_step = self._turns_taken
        self._messages = list(result)

    async def _final_report(self) -> None:
        """A limit was reached while the delegate was still working: one tool-less call turns
        what it gathered into a report instead of returning its last half-thought."""
        if self._status != "cancelled":
            self._status = "partial"
        reason = self._stop_reason or "its limit"
        self._say(ChatMessage(role="user", content=FINAL_REPORT_NOTE.format(reason=reason)))
        try:
            generation = await self._generate(with_tools=False)
        except Exception as err:
            self._final_summary = f"Subagent error during its final report: {err}"
            return
        if generation.text:
            self._final_summary = generation.text

    # --- results and events -------------------------------------------------------------------

    def _result(self) -> SubagentTaskResult:
        report = self._report
        status = self._status
        if report is not None and status == "completed":
            status = report.status
        summary = (report.summary if report else "") or self._final_summary.strip()
        if report is not None:
            report.changes = self._changes
        return SubagentTaskResult(
            role=self._config.id,
            task=self._request.task,
            summary=summary or "Task completed with no further comments.",
            status=status,
            turns_taken=self._turns_taken,
            tool_calls_count=self._tool_calls_count,
            duration_ms=max(1, int((time.monotonic() - self._started) * 1000)),
            subagent_id=self._subagent_id,
            changed_files=list(self._changed),
            tokens=self._tokens,
            cost=self._cost,
            branch=self._branch,
            report=report,
            model=self._model,
            isolation=self._config.isolation,
            background=self._request.background,
            parent_tool_call_id=self._events.parent_tool_call_id,
        )

    async def _announce_started(self) -> None:
        await self._events.emit(
            "subagent_started",
            {
                "subagent_id": self._subagent_id,
                "role": self._config.id,
                "name": self._config.name,
                "task": self._request.task,
                "parent_tool_call_id": self._events.parent_tool_call_id,
                "model": self._model,
                "isolation": self._config.isolation,
                "background": self._request.background,
            },
        )

    async def _announce_completed(self, result: SubagentTaskResult) -> None:
        await self._events.emit(
            "subagent_completed",
            {
                "subagent_id": self._subagent_id,
                "role": self._config.id,
                "task": self._request.task,
                "summary": result.summary,
                "duration_ms": result.duration_ms,
                "turns_taken": result.turns_taken,
                "tool_calls_count": result.tool_calls_count,
                "status": result.status,
                "tokens": result.tokens,
                "cost": result.cost,
                "changed_files": result.changed_files,
                "artefacts": result.artefacts,
                "parent_tool_call_id": result.parent_tool_call_id,
                "model": result.model,
                "branch": result.branch,
                "report": report_to_dict(result.report),
            },
        )

    async def _persist_transcript(self, result: SubagentTaskResult) -> None:
        ctx = current_execution_ctx.get()
        persist = ctx.callbacks.subagent_transcript_persister if ctx is not None else None
        if ctx is None or persist is None:
            return
        record = SubagentTranscriptRecord(
            id=f"sat_{uuid.uuid4().hex[:16]}",
            session_id=self._session_id,
            org_id=ctx.tenant_org_id,
            subagent_id=self._subagent_id,
            role=self._config.id,
            task=self._request.task,
            summary=result.summary,
            status=result.status,
            turns_taken=result.turns_taken,
            tool_calls_count=result.tool_calls_count,
            duration_ms=result.duration_ms,
            tokens=result.tokens,
            cost_usd=result.cost,
            changed_files=result.changed_files,
            artefacts=result.artefacts,
            messages=[
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_calls": m.tool_calls,
                    "tool_call_id": m.tool_call_id,
                }
                for m in self._record
            ],
            created_at=int(time.time()),
            report=report_to_dict(result.report),
            model=self._model,
        )
        try:
            await persist(record)
        except Exception:
            _count_error("saving the transcript", self._subagent_id)

    # --- model calls ------------------------------------------------------------------------

    async def _generate(self, with_tools: bool = True) -> _Generation:
        turn_ctx = current_execution_ctx.get()
        gen = turn_ctx.policy.generation if turn_ctx else None
        reasoning_effort = self._config.reasoning_effort or (gen.reasoning_effort if gen else None)
        thinking_budget = gen.thinking_budget_tokens if gen else None
        if gen and gen.reasoning:
            reasoning_effort = reasoning_effort or gen.reasoning.effort
            thinking_budget = (
                thinking_budget if thinking_budget is not None else gen.reasoning.budget_tokens
            )
        model_request = ModelRequest(
            model=self._model,
            # A copy: the conversation keeps growing after the request is sent.
            messages=list(self._messages),
            tools=[*self._tools, self._report_tool] if with_tools else None,
            temperature=self._config.temperature,
            max_tokens=gen.max_tokens if gen else None,
            reasoning_effort=reasoning_effort,
            thinking_budget_tokens=thinking_budget,
            fallback_models=list(gen.fallback_models) if gen else [],
            # Subagent requests inherit the turn's model data-governance requirement.
            zero_retention=turn_ctx.zero_retention if turn_ctx else False,
            region=turn_ctx.region if turn_ctx else "",
        )
        generation, _ = await execute_stream_with_retry(
            gateway=self._runner.gateway,
            request=model_request,
            collector_factory=_Generation,
            feed_chunk=self._feed_generation_chunk,
            session_id=self._session_id,
            event_queue=None,
        )
        await self._events.flush()
        await self._record_usage(generation)
        return generation

    async def _feed_generation_chunk(self, generation: _Generation, chunk: Any) -> None:
        if getattr(chunk, "error", None):
            raise ModelExecutionError(chunk.error, getattr(chunk, "refusal", None))
        if getattr(chunk, "token_usage", None):
            generation.usage = chunk.token_usage
            generation.cost_usd = getattr(chunk, "cost_usd", None)
        if getattr(chunk, "finish_reason", None):
            generation.finish_reason = chunk.finish_reason
        if getattr(chunk, "reasoning_blocks", None):
            merge_reasoning_blocks(generation.reasoning_blocks, chunk.reasoning_blocks)
        if getattr(chunk, "reasoning_delta", None):
            generation.reasoning += chunk.reasoning_delta
            await self._events.progress(chunk.reasoning_delta, is_reasoning=True)
        if getattr(chunk, "text_delta", None):
            generation.text += chunk.text_delta
            await self._events.progress(chunk.text_delta, is_reasoning=False)
        if getattr(chunk, "tool_call_delta", None):
            generation.add_tool_call_delta(chunk.tool_call_delta)

    def _usage_of(self, generation: _Generation) -> tuple[int, int, int, int]:
        if not generation.usage:
            prompt, completion = estimated_usage(
                self._model, self._messages, self._tools, generation.produced_text()
            )
            return prompt, completion, 0, 0
        usage = generation.usage
        return (
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            int(usage.get("cache_read_tokens") or usage.get("cache_read_input_tokens") or 0),
            int(
                usage.get("cache_creation_tokens") or usage.get("cache_creation_input_tokens") or 0
            ),
        )

    async def _record_usage(self, generation: _Generation) -> None:
        """Count the child's call against its own budget, the turn's, and the ledger."""
        prompt, completion, cache_read, cache_creation = self._usage_of(generation)
        cost = (
            generation.cost_usd
            if generation.cost_usd is not None
            else estimate_cost_usd(
                self._model,
                prompt,
                completion,
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_creation,
            )
        )
        self._tokens += prompt + completion
        self._cost += cost
        ctx = current_execution_ctx.get()
        if ctx is not None:
            ctx.turn_spend.add_subagent(self._subagent_id, cost)
        await self._events.model_usage(
            {
                "model": self._model,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": cache_creation,
                "cost_usd": cost,
            }
        )
        gateway = self._runner.gateway
        if not hasattr(gateway, "record_usage") or getattr(gateway, "records_usage", False):
            return
        try:
            await gateway.record_usage(
                tenant_org_id=ctx.tenant_org_id if ctx else "default_org",
                tenant_user_id=ctx.tenant_user_id if ctx else "dev_user",
                model=self._model,
                prompt_tokens=prompt,
                completion_tokens=completion,
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_creation,
            )
        except Exception:
            # Accounting must never fail the delegated task itself.
            _count_error("recording usage", self._subagent_id)

    # --- tool calls -------------------------------------------------------------------------

    def _redact(self, raw: str) -> str:
        ctx = current_execution_ctx.get()
        redactor = ctx.callbacks.redact_tool_output if ctx and ctx.callbacks else None
        if redactor is None:
            return raw
        result = redactor(raw)
        return result[0] if isinstance(result, tuple) else str(result)

    async def _run_tool_calls(self, generation: _Generation) -> None:
        calls = list(generation.tool_calls.values())
        for call in calls:
            call["id"] = call.get("id") or f"call_{uuid.uuid4().hex[:8]}"
        self._leaked_retries = 0
        prior_messages = list(self._messages)
        self._say(
            ChatMessage(
                role="assistant",
                content=generation.text or None,
                reasoning=generation.reasoning or None,
                reasoning_blocks=generation.reasoning_blocks or None,
                tool_calls=[
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": self._redact(call.get("arguments") or "{}"),
                        },
                    }
                    for call in calls
                ],
            )
        )
        reports = [c for c in calls if c.get("name") == self._report_tool.name]
        others = [c for c in calls if c.get("name") != self._report_tool.name]
        results = dict(
            zip(
                [c["id"] for c in others],
                await asyncio.gather(*(self._execute(call) for call in others)),
                strict=True,
            )
        )
        for call in reports:
            results[call["id"]] = self._accept_report(call)
        self._record_results(calls, results, prior_messages)

    def _record_results(
        self, calls: list[dict[str, Any]], results: dict[str, str], prior: list[ChatMessage]
    ) -> None:
        signatures: list[tuple[str, str]] = []
        mutated = False
        for call in calls:
            self._tool_calls_count += 1
            name = str(call.get("name") or "unknown")
            arguments = str(call.get("arguments") or "{}")
            signatures.append((name, arguments))
            mutated = mutated or name in EDIT_TOOLS
            result = results[call["id"]]
            if note := repeat_note(prior, name, arguments, result):
                result = f"{result}\n\n{note}"
            self._say(ChatMessage(role="tool", content=result, tool_call_id=call["id"]))
        if detected := self._progress_tracker.record_step_signature(signatures, mutated=mutated):
            if detected.suggested_nudge:
                self._say(ChatMessage(role="user", content=detected.suggested_nudge))

    def _accept_report(self, call: dict[str, Any]) -> str:
        try:
            arguments = json.loads(call.get("arguments") or "{}")
        except json.JSONDecodeError as err:
            return (
                f"Rejected: the report's arguments are not valid JSON ({err}). Call `report` again."
            )
        if not isinstance(arguments, dict):
            return "Rejected: the report must be an object. Call `report` again."
        if problem := schema_problem(arguments.get("data"), self._config.output_schema):
            return f"Rejected: {problem}. Call `report` again."
        self._report = parse_report(arguments)
        return "Report received."

    async def _execute(self, call: dict[str, Any]) -> str:
        name = call.get("name") or "unknown"
        raw_args = call.get("arguments") or "{}"
        started = time.monotonic()
        await self._events.tool_started(call["id"], name, self._redact(raw_args))
        output, status = await self._run_tool(name, raw_args, call["id"])
        await self._events.tool_completed(
            call["id"],
            name,
            self._redact(raw_args),
            output,
            status,
            int((time.monotonic() - started) * 1000),
        )
        return output

    async def _run_tool(self, name: str, raw_args: str, call_id: str) -> tuple[str, str]:
        if name not in self._permitted:
            # Advertising a whitelist is not enforcement: the model can name any tool.
            return (
                f"Blocked: the '{name}' tool is not available to the '{self._config.id}' subagent.",
                "error",
            )
        try:
            args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError as err:
            return f"Error: the arguments are not valid JSON ({err}).", "error"
        if name in EDIT_TOOLS:
            target = str(args.get("path") or args.get("file_path") or args.get("target_file") or "")
            if target and target not in self._edited:
                self._edited.append(target)
        dispatcher = self._runner.dispatcher
        try:
            raw_out = await dispatcher.run_guarded(self._sandbox_id, name, args)
        except TimeoutError:
            return f"Tool execution timed out after {dispatcher.tool_timeout_seconds}s", "error"
        except Exception as err:
            return f"Tool execution error: {err}", "error"
        output = self._redact(raw_out) if isinstance(raw_out, str) else str(raw_out)
        if name not in ("file_read", "read_file"):
            output = await offload_tool_output(
                output, self._sandbox_id, call_id, driver=self._runner.registry._driver
            )
        return clamp_output(output), "completed"
