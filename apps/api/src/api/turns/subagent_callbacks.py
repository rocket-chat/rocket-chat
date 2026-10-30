"""What a turn gives its subagents: the organization's roles, a model per tier, memories for the
task, supervised background runs, a sandbox of their own, how many writers may run at once, and
where a background child's events go once its turn has ended."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypedDict

from agent_core.subagent_events import CHILD_TOOL_COMPLETED, CHILD_TOOL_STARTED
from agent_core.subagent_roles import load_repository_roles, merge_repository_roles
from sandbox_driver.profiles import parse_memory_mb

from api.services.memory import MemoryPolicy, MemoryService, format_recall_block
from api.services.model_defaults import model_for_tier
from api.services.session_support import active_repository_of
from api.services.settings import effective_settings, load_org_config
from api.services.subagent_runs import get_subagent_run_store
from api.services.subagent_transcripts import get_subagent_transcript_store
from api.services.subagents import load_subagent_configs
from api.turns.context import TurnContext
from api.turns.provisioning import child_workspace_spec, session_sandbox_resources
from specifications.interfaces.agent import (
    AgentEvent,
    SubagentConfig,
    SubagentRunStoreProtocol,
    SubagentTranscriptRecord,
)
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.sandbox import SandboxStatus, WorkspaceSpec

logger = logging.getLogger(__name__)

MEMORIES_RECALLED = 5
# A writer subagent needs about this much of the session sandbox to build and test without
# starving its siblings (compilers and test runners are the usual memory hogs).
CPU_PER_WRITER = 2.0
MEMORY_MB_PER_WRITER = 4096.0
MAX_PARALLEL_WRITERS = 4
_FORWARDED = {
    "subagent_started": EventType.SUBAGENT_STARTED,
    "subagent_progress": EventType.SUBAGENT_PROGRESS,
    "subagent_completed": EventType.SUBAGENT_COMPLETED,
}


class SubagentCallbacks(TypedDict):
    subagent_configs: Mapping[str, SubagentConfig]
    subagent_model: Callable[[SubagentConfig], str]
    subagent_runs: SubagentRunStoreProtocol
    subagent_memories: Callable[[str], Awaitable[str]]
    subagent_sandbox_spec: Callable[[str], Awaitable[WorkspaceSpec]] | None
    max_parallel_writers: int
    subagent_event_sink: Callable[[AgentEvent], Awaitable[None]]
    subagent_transcript_persister: Callable[[SubagentTranscriptRecord], Awaitable[None]]


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {"raw": str(raw)}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def child_step(event: AgentEvent) -> tuple[EventType, dict[str, Any]] | None:
    """A child's own step as the session sees it: ``tool_started`` / ``tool_completed`` with
    the child's id and the parent call it nests under. Never one of the parent's own steps."""
    payload = event.payload
    identity = {
        "subagent_id": str(payload.get("subagent_id") or ""),
        "parent_tool_call_id": payload.get("parent_tool_call_id"),
    }
    base = {
        "id": payload.get("id"),
        "name": payload.get("name", ""),
        "arguments": _arguments(payload.get("arguments")),
        "created_at": int(time.time()),
        **identity,
    }
    if event.event_type == CHILD_TOOL_STARTED:
        return EventType.TOOL_STARTED, base
    if event.event_type == CHILD_TOOL_COMPLETED:
        return EventType.TOOL_COMPLETED, {
            **base,
            "result": payload.get("result", ""),
            "status": payload.get("status", "completed"),
            "duration_ms": payload.get("duration_ms", 0),
        }
    return None


def session_event(event: AgentEvent) -> tuple[EventType, dict[str, Any]] | None:
    """What a background child's event becomes on the session's bus (None: not published)."""
    if event.event_type in _FORWARDED:
        return _FORWARDED[event.event_type], event.payload
    if event.event_type == "command_output":
        return EventType.LOG_CHUNK, {
            "text": str(event.payload.get("text") or ""),
            "tool_call_id": str(event.payload.get("tool_call_id") or ""),
        }
    return child_step(event)


def parallel_writers(cpu_cores: float, memory_limit: str) -> int:
    """How many children that edit or run commands fit in the session's sandbox at once."""
    by_cpu = int(cpu_cores // CPU_PER_WRITER)
    by_memory = int(parse_memory_mb(memory_limit) // MEMORY_MB_PER_WRITER)
    return max(1, min(by_cpu, by_memory, MAX_PARALLEL_WRITERS))


def _model_resolver(ctx: TurnContext, models: Mapping[str, Any]) -> Callable[[SubagentConfig], str]:
    def subagent_model(config: SubagentConfig) -> str:
        """A role's own model, else its tier: frontier is the turn's model, fast the
        organization's fast model, default what the routing mode gives subagents."""
        if config.model:
            return config.model
        if config.tier == "frontier":
            return ctx.effective_model
        if config.tier == "fast":
            return model_for_tier("fast", ctx.effective_model, models)
        router = ctx.binding.model_router
        if router is not None:
            return str(router.model_for(f"subagent:{config.id}"))
        return ctx.effective_model

    return subagent_model


def _memories(ctx: TurnContext) -> Callable[[str], Awaitable[str]]:
    db_mgr, org = ctx.req.db_mgr, ctx.session.tenant_org_id
    requester = ctx.binding.policy.attribution.requester_user_id or ctx.session.tenant_user_id

    async def subagent_memories(task: str) -> str:
        if db_mgr is None:
            return ""
        conf, _ = await load_org_config(db_mgr, org, "memory")
        service = MemoryService(db_mgr, policy=MemoryPolicy.from_settings(conf))
        records = await service.recall(
            org_id=org, user_id=requester, query=task, limit=MEMORIES_RECALLED
        )
        return format_recall_block(records)

    return subagent_memories


def _event_sink(ctx: TurnContext) -> Callable[[AgentEvent], Awaitable[None]]:
    async def sink(event: AgentEvent) -> None:
        mapped = session_event(event)
        if mapped is None:
            return
        event_type, payload = mapped
        await ctx.req.event_bus.publish(
            SessionEvent(session_id=ctx.session_id, event_type=event_type, payload=payload)
        )

    return sink


def _sandbox_spec(ctx: TurnContext) -> Callable[[str], Awaitable[WorkspaceSpec]] | None:
    if ctx.req.driver is None:
        return None

    async def spec(child_id: str) -> WorkspaceSpec:
        return await child_workspace_spec(
            ctx.req.store,
            ctx.req.db_mgr,
            ctx.session_id,
            child_id,
            ctx.binding,
            ctx.governance.egress.allowed_cidrs,
            ctx.governance.egress.block_internal_subnets,
        )

    return spec


async def _writers(ctx: TurnContext) -> int:
    if ctx.req.driver is None:
        return 1
    try:
        resources = await session_sandbox_resources(ctx.req.db_mgr, ctx.session, ctx.binding)
    except Exception:
        logger.warning(
            "Could not size the sandbox of %s for subagents", ctx.session_id, exc_info=True
        )
        return 1
    return parallel_writers(resources.cpu_cores, resources.memory_limit)


async def _roles(ctx: TurnContext) -> Mapping[str, SubagentConfig]:
    """The organization's roles, plus those the active repository defines (``.rocket/agents``)
    when its sandbox is already up (a turn never wakes a sandbox just to read them)."""
    configs = await load_subagent_configs(ctx.req.db_mgr, ctx.session.tenant_org_id)
    driver, folder = ctx.req.driver, active_repository_of(ctx.session)
    if driver is None or not folder or ctx.session.sandbox_status != SandboxStatus.RUNNING:
        return configs
    try:
        found = await load_repository_roles(driver, ctx.session_id, folder)
    except Exception:
        logger.warning("Could not read the subagent roles of %s", folder, exc_info=True)
        return configs
    return merge_repository_roles(configs, found)


async def subagent_callbacks(ctx: TurnContext) -> SubagentCallbacks:
    db_mgr, org = ctx.req.db_mgr, ctx.session.tenant_org_id
    models = await effective_settings(db_mgr, org, "models")
    return SubagentCallbacks(
        subagent_configs=await _roles(ctx),
        subagent_model=_model_resolver(ctx, models),
        subagent_runs=get_subagent_run_store(db_mgr),
        subagent_memories=_memories(ctx),
        subagent_sandbox_spec=_sandbox_spec(ctx),
        max_parallel_writers=await _writers(ctx),
        subagent_event_sink=_event_sink(ctx),
        subagent_transcript_persister=get_subagent_transcript_store(db_mgr).save_transcript,
    )
