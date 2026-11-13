"""One proxied tool call: an execution context of its own, the sandbox kept awake, the dispatch.

The call goes through the orchestrator's ``ToolDispatcher`` and the tool registry exactly like a
step of an agent turn (scope and capability gates, permission mode and approval rules, the
organization's hooks, redaction, offloading of long output), but nothing around it is a turn:
no trace, no prompt, no title, no summary, no follow-up and no continuation. The call runs under
the model-free scope, so a model or embedding call anywhere below it is refused.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from agent_core.context import ExecutionContext, current_execution_ctx

from api.sandbox_mcp.callbacks import CallScope, model_free_callbacks
from api.sandbox_mcp.platform import Platform
from api.services.audit import AuditService, GovernancePolicy
from api.services.git_credentials import inject_git_credentials_into_sandbox
from api.services.git_gateway_env import github_api_url_of
from api.services.redaction import RedactionPolicy, Redactor
from api.services.sandbox_catalogue import SandboxCatalogue, SandboxEntry
from api.services.session_env import agent_env_of, apply_session_env
from api.services.session_git import session_git
from api.services.session_support import active_repository_of
from api.services.settings import effective_settings, load_org_config
from api.services.teams import turn_teams
from api.services.turn_service import parse_hooks, permission_mode_for
from api.services.turn_trace import LEASE_HEARTBEAT_SECONDS, LEASE_TTL_SECONDS
from api.turns.events import PUBLISHING_TOOLS, published_result
from specifications.interfaces.agent import AgentEvent, ModelAttribution, TurnPolicy
from specifications.interfaces.llm import MODEL_FREE_CALL_ROLE, ModelScope, current_model_scope
from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.sandbox_mcp import (
    SANDBOX_MCP_TOOLS,
    SandboxMcpError,
    ToolCallOutcome,
)
from specifications.interfaces.session import SessionRecord

# The driver keeps a hold per reason: a call's hold never releases a turn's or a process's.
HOLD_REASON = "sandbox_mcp"
# Session metadata: when the MCP last called a tool in the session (Unix seconds).
LAST_CALL_AT = "sandbox_mcp_last_call_at"
# How often a long call tells the other replicas it is still running (it touches the session,
# whose ``updated_at`` the idle reaper reads); the reaper's pause delay is minutes long.
ACTIVITY_TOUCH_SECONDS = 60.0

# How the platform's tools word a call they refused or could not carry out ("Error: ...",
# "Error reading file ...", "Blocked: ...", "Failed to apply patch ..."). The dispatcher counts it
# as completed, since an agent reads the text either way; an MCP client is told it failed. A
# command that ran and exited non-zero is a result, not a failure.
_FAILURE_PREFIXES = ("Error", "Blocked:", "Failed to ")

# Sessions whose git gateway environment this process has handed to its driver. The driver
# keeps a session's environment in memory, so a process that did not provision the sandbox (a
# restart, another replica) configures it once before its first call, as a turn does at start.
_git_configured: set[str] = set()


async def run_tool_call(
    platform: Platform,
    record: SessionRecord,
    requester_id: str,
    name: str,
    arguments: dict[str, Any],
) -> ToolCallOutcome:
    """Run ``name`` in the session's sandbox for ``requester_id`` and return its result."""
    if name not in SANDBOX_MCP_TOOLS:
        raise SandboxMcpError(f"'{name}' is not a sandbox MCP tool.")
    if record.is_processing:
        # One writer at a time: the session's own agent is working in this sandbox.
        raise SandboxMcpError(
            "An agent turn is running in this sandbox; call again once it has finished."
        )
    exec_ctx, scope = await _execution_context(platform, record, requester_id)
    await _configure_git_once(scope)
    model_scope = ModelScope(
        org_id=record.tenant_org_id,
        user_id=record.tenant_user_id,
        session_id=record.session_id,
        requester_user_id=requester_id,
        call_role=MODEL_FREE_CALL_ROLE,
    )
    ctx_token = current_execution_ctx.set(exec_ctx)
    scope_token = current_model_scope.set(model_scope)
    try:
        async with _kept_awake(platform, record):
            return await _dispatch(platform, exec_ctx, name, arguments)
    finally:
        current_model_scope.reset(scope_token)
        current_execution_ctx.reset(ctx_token)


def forget_session(session_id: str) -> None:
    """Drop what this process remembers about a closed sandbox."""
    _git_configured.discard(session_id)


async def _dispatch(
    platform: Platform, exec_ctx: ExecutionContext, name: str, arguments: dict[str, Any]
) -> ToolCallOutcome:
    call = {"id": f"mcp_{uuid.uuid4().hex[:12]}", "name": name, "arguments": json.dumps(arguments)}
    _, _, output, duration_ms = await platform.dispatcher.execute_single_tool(
        exec_ctx.session_id, call, exec_ctx.event_queue
    )
    status = _status_of(exec_ctx.event_queue) or "completed"
    await _audit(platform, exec_ctx, name, status, duration_ms, output)
    return ToolCallOutcome(
        text=output,
        is_error=status != "completed" or output.startswith(_FAILURE_PREFIXES),
        duration_ms=duration_ms,
    )


async def _audit(
    platform: Platform,
    exec_ctx: ExecutionContext,
    name: str,
    status: str,
    duration_ms: int,
    output: str,
) -> None:
    """The audit rows a turn writes for the same call: the call, and a publication."""
    audit = AuditService(platform.db_mgr)
    actor = exec_ctx.policy.attribution.requester_user_id
    await audit.record_tool_call(
        org_id=exec_ctx.tenant_org_id,
        actor_user_id=actor,
        session_id=exec_ctx.session_id,
        tool_name=name,
        status=status,
        duration_ms=duration_ms,
    )
    if name in PUBLISHING_TOOLS and published_result(output):
        await audit.record_git_publish(
            org_id=exec_ctx.tenant_org_id,
            actor_user_id=actor,
            session_id=exec_ctx.session_id,
            tool_name=name,
            outcome=output.splitlines()[0],
        )


def _status_of(events: asyncio.Queue[AgentEvent | None]) -> str | None:
    """The dispatcher's verdict on the call (``completed``, ``error``, ``timeout``).

    The queue holds the call's own events only (a context per call) and nobody consumes them:
    the cockpit is not streamed MCP calls, so they are read once here and dropped.
    """
    status: str | None = None
    while not events.empty():
        event = events.get_nowait()
        if event is not None and event.event_type == "tool_completed":
            status = str(event.payload.get("status") or "")
    return status


async def _execution_context(
    platform: Platform, record: SessionRecord, requester_id: str
) -> tuple[ExecutionContext, CallScope]:
    """The context a turn would give this tool, built from the organization's policy alone."""
    org = record.tenant_org_id
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    governance_settings, _ = await load_org_config(platform.db_mgr, org, "governance")
    sandbox_settings = await effective_settings(platform.db_mgr, org, "sandboxes")
    general = await effective_settings(platform.db_mgr, org, "general")
    git = await session_git(platform.db_mgr, platform.git_engine, record, requester_id, None)
    sandbox_type = await _sandbox_type(platform, record, requester_id)
    apply_session_env(platform.driver, record.session_id, sandbox_type, metadata)
    capabilities = sandbox_type.capability_set if sandbox_type else frozenset[str]()
    policy = TurnPolicy(
        git=replace(git.policy, gitlab_token=git.bundle.gitlab_token),
        hooks=parse_hooks(governance_settings.get("hooks")),
        permission_mode=await permission_mode_for(
            platform.db_mgr, org, requester_id, None, metadata.get("permission_mode")
        ),
        sandbox_capabilities=capabilities,
        active_repository=active_repository_of(record),
        sandbox_env_names=tuple(v["name"] for v in sandbox_type.env) if sandbox_type else (),
        agent_env=agent_env_of(metadata),
        output_offload_threshold=int(general.get("output_offload_threshold", 6000)),
        attribution=ModelAttribution(requester_user_id=requester_id),
    )
    scope = CallScope(
        platform=platform,
        record=record,
        requester_id=requester_id,
        governance=GovernancePolicy.from_settings(governance_settings),
        git_bundle=git.bundle,
        publishes_as_member=git.policy.publishes_as_member,
        sandbox_settings=sandbox_settings,
    )
    redactor = Redactor(RedactionPolicy.from_settings(governance_settings))
    exec_ctx = ExecutionContext(
        session_id=record.session_id,
        event_queue=asyncio.Queue(),
        parent_model="",
        tenant_org_id=org,
        tenant_user_id=record.tenant_user_id,
        allowed_tools=frozenset(SANDBOX_MCP_TOOLS),
        callbacks=model_free_callbacks(scope, redactor.redact_text),
        policy=policy,
        sandbox_capabilities=capabilities,
        active_repository=policy.active_repository,
        agent_env=dict(policy.agent_env),
        # A turn snapshots the workspace before its first change so a person can rewind it. The
        # calling agent keeps its own history, and a snapshot before every call would be noise.
        checkpointed=True,
    )
    return exec_ctx, scope


async def _sandbox_type(
    platform: Platform, record: SessionRecord, requester_id: str
) -> SandboxEntry | None:
    """The session's sandbox type, as a turn resolves it (a team's type for its members only)."""
    catalogue = SandboxCatalogue(platform.db_mgr)
    org = record.tenant_org_id
    teams = await turn_teams(platform.db_mgr, org, requester_id, None)
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    if sandbox_id := metadata.get("sandbox_id"):
        if entry := await catalogue.resolve(org, sandbox_id=str(sandbox_id), teams=teams):
            return entry
    return await catalogue.resolve(org, image=record.container_image or None, teams=teams)


async def _configure_git_once(scope: CallScope) -> None:
    """Point a sandbox this process did not start at the git gateway, once.

    A sandbox this process provisions is configured by provisioning itself; one already running
    (started before a restart or by another replica) is configured here, like a turn's start.
    """
    platform, record = scope.platform, scope.record
    session_id = record.session_id
    # Not yet running: the call that starts it provisions it, git included.
    if (
        session_id in _git_configured
        or await platform.driver.get_status(session_id) != SandboxStatus.RUNNING
    ):
        return
    await inject_git_credentials_into_sandbox(
        platform.driver,
        session_id,
        scope.git_bundle,
        org_id=record.tenant_org_id,
        user_id=record.tenant_user_id,
        api_url=github_api_url_of(platform.git_engine),
    )
    _git_configured.add(session_id)


@contextlib.asynccontextmanager
async def _kept_awake(platform: Platform, record: SessionRecord) -> AsyncIterator[None]:
    """Hold the sandbox out of idle hibernation while the call runs, here and for the reaper.

    The driver's hold is process-local; the reaper may run on another replica, which only sees
    the session's ``updated_at``, so a long call also touches the session now and then.
    """
    driver, session_id = platform.driver, record.session_id

    async def heartbeat() -> None:
        touched = time.monotonic()
        while True:
            driver.hold_awake(session_id, seconds=LEASE_TTL_SECONDS * 2, reason=HOLD_REASON)
            await asyncio.sleep(LEASE_HEARTBEAT_SECONDS)
            if time.monotonic() - touched >= ACTIVITY_TOUCH_SECONDS:
                await _touch(platform, record)
                touched = time.monotonic()

    await _touch(platform, record)
    task = asyncio.create_task(heartbeat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        driver.hold_awake(session_id, seconds=0, reason=HOLD_REASON)
        await _touch(platform, record)


async def _touch(platform: Platform, record: SessionRecord) -> None:
    def mark(stored: SessionRecord) -> None:
        known = stored.metadata if isinstance(stored.metadata, dict) else {}
        stored.metadata = {**known, LAST_CALL_AT: int(time.time())}

    await platform.store.mutate_session(record.session_id, record.tenant_org_id, mark)
