"""The per-turn collaborators the agent's tools call back into (sandbox, KB, skills, GitHub)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from git_engine.engine import pick_installation

from api.services.artifacts import get_artifact_store
from api.services.git_credentials import GitCredentialBundle, app_may_act_on
from api.services.git_gateway_env import github_api_url_of
from api.services.github_installations import installations_for_org
from api.services.github_watches import GithubWatchStore, watch_callbacks
from api.services.knowledge import search_knowledge
from api.services.mcp_runtime import McpToolProvider
from api.services.memory import MemoryService
from api.services.process_tracker import ProcessTracker
from api.services.session_env import remember_agent_env
from api.services.session_support import choose_active_repository, remember_active_repository
from api.services.settings import effective_settings
from api.services.skills import test_skill_execution
from api.services.skills_lookup import _find_visible_skill
from api.services.subagent_transcripts import get_subagent_transcript_store
from api.services.subagents import load_subagent_configs
from api.services.tool_loading import remember_guidance_read, remember_loaded
from api.services.waits import get_wait_store, settle_waits
from api.turns.context import TurnContext
from api.turns.provisioning import provision_session_sandbox, run_repository_setup
from api.turns.shared import _background_tasks
from specifications.interfaces.agent import GithubAccess, TurnCallbacks
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.session import (
    ArtifactStoreProtocol,
    SessionArtifact,
    SessionRecord,
)

logger = logging.getLogger(__name__)


def _schedule_time_wake(ctx: TurnContext, delay: float, wait_id: str = "") -> None:
    async def _wake() -> None:
        await asyncio.sleep(delay)
        if wait_id and ctx.req.event_bus:
            await ctx.req.event_bus.publish(
                SessionEvent(
                    session_id=ctx.session_id,
                    event_type=EventType.WAIT_COMPLETED,
                    payload={
                        "wait_id": wait_id,
                        "wait_type": "time",
                        "target": str(delay),
                        "status": "completed",
                        "summary": f"Timer delay of {delay}s expired.",
                    },
                )
            )
        from api.turns.tasks import _spawn_background_turn

        _spawn_background_turn(
            session_id=ctx.session_id,
            prompt=f"Timer delay of {delay}s expired. Resuming execution.",
            orchestrator=ctx.req.orchestrator,
            event_bus=ctx.req.event_bus,
            driver=ctx.req.driver,
            registry=ctx.req.registry,
            store=ctx.req.store,
            model=ctx.effective_model,
            git_engine=ctx.req.git_engine,
            db_mgr=ctx.req.db_mgr,
            concurrency_gate=ctx.req.concurrency_gate,
            session_locks=ctx.req.session_locks,
            synthetic_prompt=True,
            overlay=ctx.req.overlay,
        )

    task = asyncio.create_task(_wake())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _schedule_process_wake(
    ctx: TurnContext, identifier: str, timeout_seconds: int | None, wait_id: str = ""
) -> None:
    driver = ctx.req.driver
    if driver is None:
        return

    async def _wake() -> None:
        from agent_core.tools.processes import get_process, is_valid_id

        if not is_valid_id(identifier):
            logger.warning(
                "Refusing to schedule process wake for invalid process id: %s", identifier
            )
            return

        max_wait = float(timeout_seconds) if timeout_seconds else 600.0
        start = asyncio.get_running_loop().time()
        exited = False
        while (asyncio.get_running_loop().time() - start) < max_wait:
            await asyncio.sleep(2.0)
            try:
                info = await get_process(driver, ctx.session_id, identifier)
                if info is not None and not info.running:
                    exited = True
                    break
            except Exception as err:
                logger.warning("Error inspecting process %s: %s", identifier, err)
                break

        if wait_id and ctx.req.event_bus:
            await ctx.req.event_bus.publish(
                SessionEvent(
                    session_id=ctx.session_id,
                    event_type=EventType.WAIT_COMPLETED,
                    payload={
                        "wait_id": wait_id,
                        "wait_type": "process_exit",
                        "target": identifier,
                        "status": "completed" if exited else "timeout",
                        "summary": (
                            f"Background process {identifier} completed."
                            if exited
                            else f"Wait timeout of {int(max_wait)}s expired for process {identifier}."
                        ),
                    },
                )
            )

        from api.turns.tasks import _spawn_background_turn

        outcome = "completed" if exited else f"timed out after {int(max_wait)}s"
        _spawn_background_turn(
            session_id=ctx.session_id,
            prompt=f"Background process {identifier} {outcome}. Resuming execution.",
            orchestrator=ctx.req.orchestrator,
            event_bus=ctx.req.event_bus,
            driver=ctx.req.driver,
            registry=ctx.req.registry,
            store=ctx.req.store,
            model=ctx.effective_model,
            git_engine=ctx.req.git_engine,
            db_mgr=ctx.req.db_mgr,
            concurrency_gate=ctx.req.concurrency_gate,
            session_locks=ctx.req.session_locks,
            synthetic_prompt=True,
            overlay=ctx.req.overlay,
        )

    task = asyncio.create_task(_wake())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# Right after a push GitHub takes a few seconds to create a commit's checks.
NO_CHECKS_GRACE_SECONDS = 15.0
NO_CHECKS_ATTEMPTS = 4


async def _ci_already_settled(
    ctx: TurnContext, git_bundle: GitCredentialBundle | None, repo: str, pr_num: int
) -> str | None:
    """What to tell the agent instead of waiting, when CI has already finished or never runs.

    A wait on finished CI only woke the session at its timeout (30 minutes and more), and a
    wait on a pull request with no CI at all could never end any other way.
    """
    for attempt in range(NO_CHECKS_ATTEMPTS):
        status, summary, settled = await _check_github_ci_poll(ctx, git_bundle, repo, pr_num)
        if settled:
            return f"{summary} No wait was registered: act on this result now."
        if status != "none":
            return None
        if attempt < NO_CHECKS_ATTEMPTS - 1:
            await asyncio.sleep(NO_CHECKS_GRACE_SECONDS)
    return (
        f"{summary} No wait was registered: read the pull request's state "
        "(`github_pull_request`) and decide what to do instead of waiting for CI."
    )


async def _check_github_ci_poll(
    ctx: TurnContext,
    git_bundle: GitCredentialBundle | None,
    repo: str,
    pr_num: int,
) -> tuple[str, str, bool]:
    if git_bundle is None:
        return "", "", False
    access_fn = _github_read_access(ctx, git_bundle)
    access = await access_fn(repo)
    if not access or not access.token:
        return "", "", False
    try:
        from agent_core.tools.github_client import request as gh_request

        pull = await gh_request(access, "GET", f"/repos/{repo}/pulls/{pr_num}")
        head_sha = (pull.get("head") or {}).get("sha")
        if not head_sha:
            return "", "", False
        runs_data = await gh_request(
            access, "GET", f"/repos/{repo}/commits/{head_sha}/check-runs", params={"per_page": 100}
        )
        check_runs = runs_data.get("check_runs", [])
        status_data = await gh_request(access, "GET", f"/repos/{repo}/commits/{head_sha}/status")
        statuses = status_data.get("statuses", [])

        failed = [
            r.get("name") or "unnamed"
            for r in check_runs
            if r.get("conclusion") in ("failure", "timed_out", "cancelled", "action_required")
        ] + [
            s.get("context") or "unnamed"
            for s in statuses
            if s.get("state") in ("failure", "error")
        ]
        if failed:
            return "failed", f"CI failed on {repo}#{pr_num} ({', '.join(failed[:5])})", True

        in_progress = any(
            r.get("status") in ("in_progress", "queued", "pending") for r in check_runs
        ) or any(s.get("state") == "pending" for s in statuses)

        total = len(check_runs) + len(statuses)
        if total > 0 and not in_progress:
            return "completed", f"All {total} CI check(s) passed on {repo}#{pr_num}.", True
        if total == 0:
            behind = pull.get("mergeable_state") == "behind"
            return (
                "none",
                f"No CI check or status is reported on {repo}#{pr_num} (head {head_sha[:8]})"
                + (
                    ": the branch is behind its base, and this repository's CI may only run on "
                    "an up-to-date branch."
                    if behind
                    else ": CI may not run for this repository, branch or set of paths."
                ),
                False,
            )

        return "in_progress", "", False
    except Exception as exc:
        logger.warning("Error checking GitHub CI poll for %s#%s: %s", repo, pr_num, exc)
        return "", "", False


async def _check_github_review_poll(
    ctx: TurnContext,
    git_bundle: GitCredentialBundle | None,
    repo: str,
    pr_num: int,
) -> tuple[str, str, bool]:
    if git_bundle is None:
        return "", "", False
    access_fn = _github_read_access(ctx, git_bundle)
    access = await access_fn(repo)
    if not access or not access.token:
        return "", "", False
    try:
        from agent_core.tools.github_client import request as gh_request

        reviews = await gh_request(access, "GET", f"/repos/{repo}/pulls/{pr_num}/reviews")
        substantive = [r for r in reviews if r.get("state") in ("APPROVED", "CHANGES_REQUESTED")]
        if substantive:
            latest = substantive[-1]
            state = str(latest.get("state") or "")
            author = (latest.get("user") or {}).get("login") or "reviewer"
            return state, f"PR review submitted by {author}: {state}.", True
        return "pending", "", False
    except Exception as exc:
        logger.warning("Error checking GitHub review poll for %s#%s: %s", repo, pr_num, exc)
        return "", "", False


def _schedule_github_wake(
    ctx: TurnContext,
    target: str,
    identifier: str,
    timeout_seconds: int | None,
    git_bundle: GitCredentialBundle | None = None,
    wait_id: str = "",
) -> None:
    max_wait = float(timeout_seconds) if timeout_seconds else 1800.0
    repo, pr_num = _parse_repo_pr(
        identifier,
        default_repo=str(ctx.session.metadata.get("github_repo") or "")
        if ctx.session.metadata
        else "",
    )

    async def _wake() -> None:
        poll_interval = 300.0
        start = asyncio.get_running_loop().time()
        while (asyncio.get_running_loop().time() - start) < max_wait:
            await asyncio.sleep(
                min(poll_interval, max_wait - (asyncio.get_running_loop().time() - start))
            )
            locks = ctx.req.session_locks
            if locks is not None and locks.is_locked(ctx.session_id):
                return
            store = GithubWatchStore(ctx.req.db_mgr)
            watches = await store.for_session(ctx.session.tenant_org_id, ctx.session_id)
            matching = [w for w in watches if w.target == identifier and w.active]
            if not matching:
                return

            if pr_num is not None:
                if target == "ci_status":
                    status, summary, should_wake = await _check_github_ci_poll(
                        ctx, git_bundle, repo, pr_num
                    )
                elif target == "review":
                    status, summary, should_wake = await _check_github_review_poll(
                        ctx, git_bundle, repo, pr_num
                    )
                else:
                    status, summary, should_wake = "", "", False

                if should_wake:
                    for w in matching:
                        await store.record_delivery(
                            w, {"status": status, "summary": summary}, woke=True, finished=True
                        )
                    await settle_waits(
                        get_wait_store(ctx.req.db_mgr),
                        ctx.session_id,
                        ctx.session.tenant_org_id,
                        status="completed",
                        kind=target,
                        target=identifier,
                    )
                    await ctx.req.event_bus.publish(
                        SessionEvent(
                            session_id=ctx.session_id,
                            event_type=EventType.WAIT_COMPLETED,
                            payload={
                                "wait_id": wait_id or f"poll_{uuid.uuid4().hex[:8]}",
                                "wait_type": target,
                                "target": identifier,
                                "status": status,
                                "summary": summary,
                            },
                        )
                    )
                    from api.turns.tasks import _spawn_background_turn

                    _spawn_background_turn(
                        session_id=ctx.session_id,
                        prompt=(
                            f"{target} update for {identifier}: {summary} "
                            "Proceed with the next steps."
                        ),
                        orchestrator=ctx.req.orchestrator,
                        event_bus=ctx.req.event_bus,
                        driver=ctx.req.driver,
                        registry=ctx.req.registry,
                        store=ctx.req.store,
                        model=ctx.effective_model,
                        git_engine=ctx.req.git_engine,
                        db_mgr=ctx.req.db_mgr,
                        concurrency_gate=ctx.req.concurrency_gate,
                        session_locks=ctx.req.session_locks,
                        synthetic_prompt=True,
                        overlay=ctx.req.overlay,
                    )
                    return

        from api.turns.tasks import _spawn_background_turn

        store = GithubWatchStore(ctx.req.db_mgr)
        watches = await store.for_session(ctx.session.tenant_org_id, ctx.session_id)
        matching = [w for w in watches if w.target == identifier and w.active]
        for w in matching:
            await store.record_delivery(
                w,
                {"status": "timeout", "summary": f"Wait timeout of {int(max_wait)}s expired"},
                woke=True,
                finished=True,
            )
        await ctx.req.event_bus.publish(
            SessionEvent(
                session_id=ctx.session_id,
                event_type=EventType.WAIT_COMPLETED,
                payload={
                    "wait_id": wait_id or f"timeout_{uuid.uuid4().hex[:8]}",
                    "wait_type": target,
                    "target": identifier,
                    "status": "timeout",
                    "summary": f"Wait timeout of {int(max_wait)}s expired for {target} on {identifier}.",
                },
            )
        )
        _spawn_background_turn(
            session_id=ctx.session_id,
            prompt=(
                f"Wait timeout of {int(max_wait)}s expired for {target} on {identifier}. "
                "Resuming execution to verify status directly."
            ),
            orchestrator=ctx.req.orchestrator,
            event_bus=ctx.req.event_bus,
            driver=ctx.req.driver,
            registry=ctx.req.registry,
            store=ctx.req.store,
            model=ctx.effective_model,
            git_engine=ctx.req.git_engine,
            db_mgr=ctx.req.db_mgr,
            concurrency_gate=ctx.req.concurrency_gate,
            session_locks=ctx.req.session_locks,
            synthetic_prompt=True,
            overlay=ctx.req.overlay,
        )

    task = asyncio.create_task(_wake())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _parse_repo_pr(identifier: str, default_repo: str = "") -> tuple[str, int | None]:
    clean = identifier.strip()
    m = re.match(r"^https?://[^/]+/([^/]+/[^/]+)/pull/(\d+)", clean, re.IGNORECASE)
    if m:
        return m.group(1).lower(), int(m.group(2))
    m = re.match(r"^([^#/]+/[^#/]+)#(\d+)$", clean)
    if m:
        return m.group(1).lower(), int(m.group(2))
    m = re.match(r"^#?(\d+)$", clean)
    if m:
        return default_repo.lower(), int(m.group(1))
    if "/" in clean and "#" not in clean:
        return clean.lower(), None
    return clean.lower(), None


def _normalize_wait(
    target: str, identifier: str, timeout_seconds: int | None, default_repo: str
) -> tuple[str, int | None, str, int]:
    """The repository and pull request (for GitHub waits), the normalized target and timeout."""
    if target in ("ci_status", "review"):
        repo, pr_num = _parse_repo_pr(identifier, default_repo=default_repo)
        normalized = f"{repo}#{pr_num}" if pr_num is not None else repo
        return repo, pr_num, normalized, max(30, min(timeout_seconds or 1800, 7200))
    if target == "time":
        seconds = float(identifier) if identifier.replace(".", "", 1).isdigit() else 30.0
        delay = max(1.0, min(seconds, 86400.0))
        return "", None, str(delay), int(delay)
    return "", None, identifier, max(5, min(timeout_seconds or 600, 3600))


def _build_wait_for(
    session_id: str,
    event_bus: Any,
    github_watch: Any,
    ctx: TurnContext | None = None,
    git_bundle: GitCredentialBundle | None = None,
) -> Callable[[str, str, int | None], Awaitable[str]]:
    async def wait_for(target: str, identifier: str, timeout_seconds: int | None) -> str:
        wait_id = f"wait_{uuid.uuid4().hex[:12]}"
        default_repo = ""
        if ctx is not None and ctx.session.metadata:
            default_repo = str(ctx.session.metadata.get("github_repo") or "")

        repo, pr_num, norm_identifier, effective_timeout = _normalize_wait(
            target, identifier, timeout_seconds, default_repo
        )

        if target == "ci_status" and pr_num is not None and ctx is not None:
            if settled := await _ci_already_settled(ctx, git_bundle, repo, pr_num):
                return settled
        reason = f"{target} on {norm_identifier}"
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.WAIT_STARTED,
                payload={
                    "wait_id": wait_id,
                    "wait_type": target,
                    "target": norm_identifier,
                    "timeout_seconds": effective_timeout,
                    "reason": reason,
                },
            )
        )
        if ctx is not None:
            ctx.state.is_waiting = True
            ctx.state.wait_target = reason
            ctx.state.wait_summary = f"Waiting for {reason}. Durable wait registered; turn paused."
            if ctx.req.db_mgr:
                try:
                    from api.services.waits import PostgresWaitStore, SessionWait

                    waits = PostgresWaitStore(ctx.req.db_mgr)
                    # One wait per target: a second wait_for on the same pull request replaces
                    # the first instead of waking the session twice.
                    await settle_waits(
                        waits,
                        session_id,
                        ctx.session.tenant_org_id,
                        status="cancelled",
                        kind=target,
                        target=norm_identifier,
                    )
                    await waits.create_wait(
                        SessionWait(
                            id=wait_id,
                            session_id=session_id,
                            tenant_org_id=ctx.session.tenant_org_id,
                            kind=target,
                            target=norm_identifier,
                            deadline=int(time.time()) + effective_timeout,
                            status="waiting",
                        )
                    )
                except Exception as err:
                    logger.warning("Could not persist durable wait %s: %s", wait_id, err)

        if target in ("ci_status", "review"):
            branch = ctx.session.git_branch if (ctx is not None and pr_num is None) else None
            await github_watch(repo, pr_num, branch=branch, wake=True)
            if ctx is not None:
                _schedule_github_wake(
                    ctx,
                    target,
                    norm_identifier,
                    effective_timeout,
                    git_bundle=git_bundle,
                    wait_id=wait_id,
                )
            return (
                f"Waiting for {target} on {norm_identifier} (timeout: {effective_timeout}s). Turn paused; "
                "will automatically wake upon webhook arrival or timeout."
            )
        if target == "time":
            if ctx is not None:
                _schedule_time_wake(ctx, float(norm_identifier), wait_id=wait_id)
            return f"Waiting for timer delay of {norm_identifier}s. Turn paused; will wake after delay."
        if target == "process_exit":
            if ctx is not None:
                _schedule_process_wake(ctx, norm_identifier, effective_timeout, wait_id=wait_id)
            return (
                f"Waiting for background process {norm_identifier} to exit. Turn paused; "
                "will wake when process completes."
            )
        return f"Waiting for {target} ({norm_identifier}). Turn entering waiting state."

    return wait_for


def _truncate_artifact_preview(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _make_artifact_callbacks(
    art_store: ArtifactStoreProtocol,
    event_bus: Any,
    session_id: str,
) -> tuple[
    Callable[[SessionArtifact], Awaitable[SessionArtifact]],
    Callable[[str, str, str, str], Awaitable[SessionArtifact | None]],
]:
    async def _create(artifact: SessionArtifact) -> SessionArtifact:
        saved = await art_store.create_artifact(artifact)
        if event_bus:
            await event_bus.publish(
                SessionEvent(
                    session_id=session_id,
                    event_type=EventType.ARTIFACT_CREATED,
                    payload={
                        "id": saved.id,
                        "title": saved.title,
                        "filename": saved.filename,
                        "artifact_type": saved.artifact_type,
                        "version": saved.version,
                        "content": _truncate_artifact_preview(saved.content),
                        "metadata": saved.metadata,
                        "created_at": saved.created_at,
                    },
                ),
            )
        return saved

    async def _update(
        target_session_id: str, artifact_id: str, target_org: str, content: str
    ) -> SessionArtifact | None:
        updated = await art_store.update_artifact(
            target_session_id, artifact_id, target_org, content
        )
        if updated and event_bus:
            await event_bus.publish(
                SessionEvent(
                    session_id=session_id,
                    event_type=EventType.ARTIFACT_UPDATED,
                    payload={
                        "id": updated.id,
                        "title": updated.title,
                        "filename": updated.filename,
                        "artifact_type": updated.artifact_type,
                        "version": updated.version,
                        "content": _truncate_artifact_preview(updated.content),
                        "updated_at": updated.updated_at,
                    },
                ),
            )
        return updated

    return _create, _update


async def build_turn_callbacks(
    ctx: TurnContext, git_bundle: GitCredentialBundle, mcp_tools: McpToolProvider | None
) -> TurnCallbacks:
    """Bind this turn's tools to its session, organization and credentials."""
    req, session, binding = ctx.req, ctx.session, ctx.binding
    store, event_bus, db_mgr, driver = req.store, req.event_bus, req.db_mgr, req.driver

    async def ensure_sandbox(target_session_id: str) -> None:
        assert driver is not None
        await provision_session_sandbox(
            store=store,
            driver=driver,
            event_bus=event_bus,
            db_mgr=db_mgr,
            git_engine=req.git_engine,
            session_id=target_session_id,
            binding=binding,
            egress_allowed_cidrs=ctx.governance.egress.allowed_cidrs,
            block_internal_subnets=ctx.governance.egress.block_internal_subnets,
            fallback_user=session.tenant_user_id,
            fallback_org=session.tenant_org_id,
        )

    async def rename(target_session_id: str, new_title: str) -> None:

        def set_title(record: SessionRecord) -> None:
            record.title = new_title

        if await store.mutate_session(target_session_id, session.tenant_org_id, set_title):
            await event_bus.publish(
                SessionEvent(
                    session_id=target_session_id,
                    event_type=EventType.SESSION_UPDATED,
                    payload={"title": new_title, "session_id": target_session_id},
                )
            )

    async def kb_search(query: str, kb_id: str | None = None, limit: int = 5) -> str:
        if not db_mgr:
            return f"No knowledge base entries matched '{query}'."
        async with db_mgr.session(tenant_org_id=session.tenant_org_id) as db_session:
            return await search_knowledge(
                db_session,
                org_id=session.tenant_org_id,
                user_id=session.tenant_user_id,
                query_text=query,
                kb_ref=kb_id,
                limit=limit,
                allowed_kb_ids=binding.knowledge_base_ids,
            )

    async def execute_skill(skill_name: str, args: dict[str, Any]) -> str:
        skill = await _find_visible_skill(
            db_mgr,
            session.tenant_org_id,
            session.tenant_user_id,
            skill_name,
            allowed_ids=binding.skill_ids,
        )
        if skill is None:
            return f"Skill '{skill_name}' not found."
        if skill.runtime == "python" and skill.code:
            # Only code needs the sandbox; an instructions skill is just read back.
            if driver is not None:
                await ensure_sandbox(ctx.session_id)
            ok, out, _, _ = await test_skill_execution(
                driver, ctx.session_id, skill_name, skill.code, args
            )
            return out if ok else f"Skill execution error: {out}"
        return skill.body or f"Skill '{skill_name}' has no content."

    github_watch, github_unwatch = watch_callbacks(
        GithubWatchStore(db_mgr), session.tenant_org_id, ctx.session_id, session.tenant_user_id
    )

    sandbox_settings = await effective_settings(db_mgr, session.tenant_org_id, "sandboxes")

    async def processes_changed(target_session_id: str) -> None:
        await ProcessTracker.for_app(db_mgr, event_bus).refresh(
            target_session_id,
            session.tenant_org_id,
            driver,
            keepalive_seconds=float(sandbox_settings["process_keepalive_minutes"]) * 60,
        )

    wait_for = _build_wait_for(
        ctx.session_id, event_bus, github_watch, ctx=ctx, git_bundle=git_bundle
    )
    art_store = get_artifact_store(db_mgr)
    _create_artifact, _update_artifact = _make_artifact_callbacks(
        art_store, event_bus, ctx.session_id
    )

    # (`bind_context_memory_callbacks` in main.py); it resolves the tenant from the turn's
    # execution context, so every path (web, Slack, webhooks) shares it.
    return TurnCallbacks(
        ensure_sandbox=ensure_sandbox if driver else None,
        rename=rename,
        kb_search=kb_search,
        execute_skill=execute_skill,
        external_tools=mcp_tools,
        github_access=_github_read_access(ctx, git_bundle),
        github_write_access=_github_write_access(ctx, git_bundle),
        github_watch=github_watch,
        github_unwatch=github_unwatch,
        wait_for=wait_for,
        create_artifact=_create_artifact,
        update_artifact=_update_artifact,
        get_artifact=art_store.get_artifact,
        list_artifacts=art_store.list_artifacts,
        **_session_record_callbacks(ctx),
        processes_changed=processes_changed if driver else None,
        max_background_processes=int(sandbox_settings["max_background_processes"]),
        subagent_configs=await load_subagent_configs(db_mgr, session.tenant_org_id),
        redact_tool_output=ctx.redactor.redact_text
        if getattr(ctx, "redactor", None) is not None
        else None,
        subagent_transcript_persister=get_subagent_transcript_store(db_mgr).save_transcript,
    )


class _SessionRecordCallbacks(TypedDict):
    repository_cloned: Callable[[str, str], Awaitable[str | None]] | None
    active_repository_chosen: Callable[[str, str], Awaitable[None]] | None
    tools_loaded: Callable[[str, list[str]], Awaitable[None]]
    guidance_read: Callable[[str, dict[str, str]], Awaitable[None]]
    agent_env_changed: Callable[[str, dict[str, str]], Awaitable[None]]


def _session_record_callbacks(ctx: TurnContext) -> _SessionRecordCallbacks:
    """What the turn's tools record on the session for its later turns."""
    req, session = ctx.req, ctx.session
    store, driver, org = req.store, req.driver, session.tenant_org_id

    async def repository_cloned(target_session_id: str, folder: str) -> str | None:
        assert driver is not None
        await remember_active_repository(store, target_session_id, org, folder)
        notes: list[str] = []
        sandbox_cfg = await effective_settings(req.db_mgr, org, "sandboxes")
        if sandbox_cfg.get("run_repo_setup"):
            setup_res = await run_repository_setup(driver, target_session_id, folder, req.event_bus)
            if setup_res:
                notes.append(setup_res)
        if proj_mem := await _recall_project_memory_for_repo(
            req.db_mgr, org, session.tenant_user_id, folder
        ):
            notes.append(proj_mem)
        try:
            from agent_core.tools.builtin.workspace import sync_workspace_manifest

            await sync_workspace_manifest(driver, target_session_id, active_repo=folder)
        except Exception:
            pass
        return "\n\n".join(notes) if notes else None

    async def active_repository_chosen(target_session_id: str, folder: str) -> None:
        await choose_active_repository(store, target_session_id, org, folder)

    async def tools_loaded(target_session_id: str, names: list[str]) -> None:
        await store.mutate_session(target_session_id, org, remember_loaded(names))

    async def guidance_read(target_session_id: str, read: dict[str, str]) -> None:
        await store.mutate_session(target_session_id, org, remember_guidance_read(read))

    async def agent_env_changed(target_session_id: str, env: dict[str, str]) -> None:
        await remember_agent_env(store, target_session_id, org, env)

    return _SessionRecordCallbacks(
        repository_cloned=repository_cloned if driver else None,
        active_repository_chosen=active_repository_chosen if driver else None,
        tools_loaded=tools_loaded,
        guidance_read=guidance_read,
        agent_env_changed=agent_env_changed,
    )


def _github_read_access(
    ctx: TurnContext, git_bundle: GitCredentialBundle
) -> Callable[..., Awaitable[GithubAccess]]:
    """Credential for read-only GitHub tools. Uses member token if linked, else dynamically
    resolves an App installation token for the requested repository or organization."""
    engine, api_url = ctx.req.git_engine, github_api_url_of(ctx.req.git_engine)
    member = git_bundle.user_token
    login = str(git_bundle.source_details.get("username") or "")
    as_member = GithubAccess(
        member, api_url, f"@{login}" if login else "your linked GitHub account"
    )
    app_actor = "the organization's GitHub App"
    bundle_actor = (
        app_actor
        if git_bundle.mode == "app"
        else (as_member.actor if git_bundle.mode == "member" else "")
    )

    async def access(repository: str | None = None) -> GithubAccess:
        if member:
            return as_member
        org = ctx.session.tenant_org_id
        if engine is not None:
            clean = (
                (repository or "")
                .strip()
                .removeprefix("https://github.com/")
                .removesuffix(".git")
                .strip("/")
            )
            github_settings = await effective_settings(ctx.req.db_mgr, org, "github")
            if not clean or app_may_act_on(github_settings, clean):
                mapped = await installations_for_org(ctx.req.db_mgr, org)
                inst_id = pick_installation(
                    [(i.installation_id, i.account_login) for i in mapped], clean or None
                )
                if inst_id is not None:
                    repos = [clean.rsplit("/", 1)[-1]] if "/" in clean else None
                    token = await engine.get_github_access_token(
                        repo=clean or None,
                        repositories=repos,
                        installation_id=inst_id,
                        inherit_permissions=True,
                    )
                    if token:
                        return GithubAccess(token, api_url, app_actor)
        if git_bundle.token:
            return GithubAccess(git_bundle.token, api_url, bundle_actor)
        return GithubAccess(None, api_url, "")

    return access


def _github_write_access(
    ctx: TurnContext, git_bundle: GitCredentialBundle
) -> Callable[[str], Awaitable[GithubAccess]]:
    """Who changes things on GitHub for this turn: the member when the organization's pull
    request policy says so, else the organization's App installation for that repository, else
    the member's own account. Resolved on the platform, never handed to the sandbox."""
    engine, api_url = ctx.req.git_engine, github_api_url_of(ctx.req.git_engine)
    member = git_bundle.user_token
    login = str(git_bundle.source_details.get("username") or "")
    as_member = GithubAccess(member, api_url, f"@{login}" if login else "your GitHub account")

    async def access(repository: str) -> GithubAccess:
        if member and ctx.binding.policy.git.pr_creation == "as_user":
            return as_member
        org = ctx.session.tenant_org_id
        if engine is not None and app_may_act_on(
            await effective_settings(ctx.req.db_mgr, org, "github"), repository
        ):
            mapped = await installations_for_org(ctx.req.db_mgr, org)
            token = await engine.get_github_access_token(
                repo=repository,
                repositories=[repository.rsplit("/", 1)[-1]],
                installation_id=pick_installation(
                    [(i.installation_id, i.account_login) for i in mapped], repository
                ),
                inherit_permissions=True,
            )
            if token:
                return GithubAccess(token, api_url, "the organization's GitHub App")
        return as_member if member else GithubAccess(None, api_url, "")

    return access


async def _recall_project_memory_for_repo(
    db_mgr: Any,
    org: str,
    user_id: str,
    folder: str,
) -> str | None:
    """Recall learned project memories (build/test/lint commands) for the cloned repository (Item I1)."""
    if db_mgr is None:
        return None
    repo_name = folder.rstrip("/").split("/")[-1]
    mem_service = MemoryService(db_mgr)
    try:
        records = await mem_service.recall(
            org_id=org,
            user_id=user_id,
            query=f"{repo_name} build test lint setup command",
            limit=5,
        )
        repo_memories = [r for r in records if repo_name.lower() in r.content.lower()]
        if not repo_memories:
            return None
        lines = [f"Project memory for {repo_name}:"]
        for r in repo_memories:
            lines.append(f"- [{r.kind}] {r.content}")
        return "\n".join(lines)
    except Exception:
        return None
