"""The per-turn collaborators the agent's tools call back into (sandbox, KB, skills, GitHub)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from git_engine.engine import pick_installation

from api.services.git_credentials import GitCredentialBundle, app_may_act_on
from api.services.git_gateway_env import github_api_url_of
from api.services.github_installations import installations_for_org
from api.services.github_watches import GithubWatchStore, watch_callbacks
from api.services.knowledge import search_knowledge
from api.services.mcp_runtime import McpToolProvider
from api.services.memory import MemoryService
from api.services.process_tracker import ProcessTracker
from api.services.session_support import choose_active_repository, remember_active_repository
from api.services.settings import effective_settings
from api.services.skills import test_skill_execution
from api.services.skills_lookup import _find_visible_skill
from api.services.subagent_transcripts import get_subagent_transcript_store
from api.services.subagents import load_subagent_configs
from api.services.tool_loading import remember_loaded
from api.turns.context import TurnContext
from api.turns.provisioning import provision_session_sandbox, run_repository_setup
from api.turns.shared import _background_tasks
from specifications.interfaces.agent import GithubAccess, TurnCallbacks
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.session import SessionRecord


def _schedule_time_wake(ctx: TurnContext, delay: float) -> None:
    async def _wake() -> None:
        await asyncio.sleep(delay)
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
        )

    task = asyncio.create_task(_wake())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _schedule_process_wake(ctx: TurnContext, identifier: str, timeout_seconds: int | None) -> None:
    driver = ctx.req.driver
    if driver is None:
        return

    async def _wake() -> None:
        max_wait = float(timeout_seconds) if timeout_seconds else 600.0
        start = asyncio.get_running_loop().time()
        while (asyncio.get_running_loop().time() - start) < max_wait:
            await asyncio.sleep(2.0)
            try:
                res = await driver.exec_command(
                    ctx.session_id, f"kill -0 {identifier} 2>/dev/null", timeout_seconds=5
                )
                if res.exit_code != 0:
                    break
            except Exception:
                break
        from api.turns.tasks import _spawn_background_turn

        _spawn_background_turn(
            session_id=ctx.session_id,
            prompt=f"Background process {identifier} completed. Resuming execution.",
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
        )

    task = asyncio.create_task(_wake())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _build_wait_for(
    session_id: str,
    event_bus: Any,
    github_watch: Any,
    ctx: TurnContext | None = None,
) -> Callable[[str, str, int | None], Awaitable[str]]:
    async def wait_for(target: str, identifier: str, timeout_seconds: int | None) -> str:
        wait_id = f"wait_{uuid.uuid4().hex[:12]}"
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.WAIT_STARTED,
                payload={
                    "wait_id": wait_id,
                    "wait_type": target,
                    "target": identifier,
                    "timeout_seconds": timeout_seconds,
                },
            )
        )
        if target in ("ci_status", "review"):
            parts = identifier.split("#")
            repo = parts[0].strip()
            pr_num = (
                int(parts[1].strip()) if len(parts) > 1 and parts[1].strip().isdigit() else None
            )
            await github_watch(repo, pr_num, branch=None, wake=True)
            return (
                f"Waiting for {target} on {identifier}. Turn paused; "
                "will automatically wake upon webhook arrival."
            )
        if target == "time":
            delay = float(identifier) if identifier.replace(".", "", 1).isdigit() else 30.0
            if ctx is not None:
                _schedule_time_wake(ctx, delay)
            return f"Waiting for timer delay of {delay}s. Turn paused; will wake after delay."
        if target == "process_exit":
            if ctx is not None:
                _schedule_process_wake(ctx, identifier, timeout_seconds)
            return (
                f"Waiting for background process {identifier} to exit. Turn paused; "
                "will wake when process completes."
            )
        return f"Waiting for {target} ({identifier}). Turn entering waiting state."

    return wait_for


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

    wait_for = _build_wait_for(ctx.session_id, event_bus, github_watch, ctx=ctx)
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

    return _SessionRecordCallbacks(
        repository_cloned=repository_cloned if driver else None,
        active_repository_chosen=active_repository_chosen if driver else None,
        tools_loaded=tools_loaded,
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
