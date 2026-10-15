"""The per-turn collaborators the agent's tools call back into (sandbox, KB, skills, GitHub)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from git_engine.engine import pick_installation

from api.services.git_credentials import GitCredentialBundle, app_may_act_on
from api.services.git_gateway_env import github_api_url_of
from api.services.github_installations import installations_for_org
from api.services.github_watches import GithubWatchStore, watch_callbacks
from api.services.knowledge import search_knowledge
from api.services.mcp_runtime import McpToolProvider
from api.services.process_tracker import ProcessTracker
from api.services.session_support import choose_active_repository, remember_active_repository
from api.services.settings import effective_settings
from api.services.skills import test_skill_execution
from api.services.skills_lookup import _find_visible_skill
from api.services.subagents import load_subagent_configs
from api.turns.context import TurnContext
from api.turns.provisioning import provision_session_sandbox, run_repository_setup
from specifications.interfaces.agent import GithubAccess, TurnCallbacks
from specifications.interfaces.events import EventType, SessionEvent
from specifications.interfaces.session import SessionRecord


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

    async def github_access() -> GithubAccess:
        return GithubAccess(
            token=git_bundle.token,
            api_url=req.git_engine.settings.github_api_url
            if req.git_engine
            else "https://api.github.com",
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
            ok, out, _, _ = await test_skill_execution(
                driver, ctx.session_id, skill_name, skill.code, args
            )
            return out if ok else f"Skill execution error: {out}"
        return skill.body or f"Skill '{skill_name}' has no content."

    async def repository_cloned(target_session_id: str, folder: str) -> str | None:
        assert driver is not None
        await remember_active_repository(store, target_session_id, session.tenant_org_id, folder)
        sandbox_cfg = await effective_settings(db_mgr, session.tenant_org_id, "sandboxes")
        if not sandbox_cfg.get("run_repo_setup"):
            return None
        return await run_repository_setup(driver, target_session_id, folder, event_bus)

    async def active_repository_chosen(target_session_id: str, folder: str) -> None:
        await choose_active_repository(store, target_session_id, session.tenant_org_id, folder)

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

    # Long-term memory is bound once at composition time
    # (`bind_context_memory_callbacks` in main.py); it resolves the tenant from the turn's
    # execution context, so every path (web, Slack, webhooks) shares it.
    return TurnCallbacks(
        ensure_sandbox=ensure_sandbox if driver else None,
        rename=rename,
        kb_search=kb_search,
        execute_skill=execute_skill,
        external_tools=mcp_tools,
        github_access=github_access,
        github_write_access=_github_write_access(ctx, git_bundle),
        github_watch=github_watch,
        github_unwatch=github_unwatch,
        repository_cloned=repository_cloned if driver else None,
        active_repository_chosen=active_repository_chosen if driver else None,
        processes_changed=processes_changed if driver else None,
        max_background_processes=int(sandbox_settings["max_background_processes"]),
        subagent_configs=await load_subagent_configs(db_mgr, session.tenant_org_id),
    )


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
