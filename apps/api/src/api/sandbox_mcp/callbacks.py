"""The collaborators the proxied tools use, and nothing that could reach a model.

An agent turn binds its tools through ``turns/callbacks.build_turn_callbacks``. That set also
recalls project memories after a clone (an embedding), searches knowledge, runs subagents and
waits that wake the agent later, so it is not reused here: this module binds only what the tools
of ``SANDBOX_MCP_TOOLS`` need, from the same platform services.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agent_core.context import current_execution_ctx
from agent_core.tools.builtin.workspace import sync_workspace_manifest

from api.sandbox_mcp.platform import Platform
from api.services.audit import GovernancePolicy
from api.services.git_credentials import GitCredentialBundle
from api.services.github_read_access import github_read_access
from api.services.github_watches import remember_pushed_head
from api.services.github_write_access import github_write_access
from api.services.process_tracker import ProcessTracker
from api.services.repository_setups import RepositorySetupStore
from api.services.repository_setups import prepare_folder as prepare_repository_folder
from api.services.session_env import remember_agent_env
from api.services.session_support import choose_active_repository, remember_active_repository
from api.services.settings import effective_settings
from api.turns.callbacks import repository_extras
from api.turns.provisioning import provision_session_sandbox, run_repository_setup
from specifications.interfaces.agent import (
    AgentBinding,
    FolderSetupOutcome,
    GithubAccess,
    ModelAttribution,
    TurnCallbacks,
    TurnPolicy,
)
from specifications.interfaces.session import SessionRecord


@dataclass(frozen=True)
class CallScope:
    """What one call resolved about its session before the tool runs."""

    platform: Platform
    record: SessionRecord
    requester_id: str
    governance: GovernancePolicy
    git_bundle: GitCredentialBundle
    publishes_as_member: bool
    sandbox_settings: Mapping[str, Any]

    @property
    def org_id(self) -> str:
        return self.record.tenant_org_id


def model_free_callbacks(
    scope: CallScope, redact: Callable[[str], tuple[str, int]]
) -> TurnCallbacks:
    """The turn collaborators of one proxied call (``redact``: the organization's redactor)."""
    return TurnCallbacks(
        ensure_sandbox=_ensure_sandbox(scope),
        github_access=_github_read(scope),
        github_write_access=_github_write(scope),
        repository_cloned=_repository_cloned(scope),
        active_repository_chosen=_active_repository_chosen(scope),
        agent_env_changed=_agent_env_changed(scope),
        branch_published=_branch_published(scope),
        prepare_folder=_prepare_folder(scope),
        processes_changed=_processes_changed(scope),
        max_background_processes=int(scope.sandbox_settings["max_background_processes"]),
        redact_tool_output=redact,
    )


def _ensure_sandbox(scope: CallScope) -> Callable[[str], Awaitable[None]]:
    platform, record = scope.platform, scope.record
    # Provisioning reads the requester (team types, quotas) from an agent binding; the sandbox
    # MCP has no agent, so the binding carries the caller and nothing else.
    binding = AgentBinding(
        id="", policy=TurnPolicy(attribution=ModelAttribution(requester_user_id=scope.requester_id))
    )

    async def ensure(session_id: str) -> None:
        await provision_session_sandbox(
            store=platform.store,
            driver=platform.driver,
            event_bus=platform.event_bus,
            db_mgr=platform.db_mgr,
            git_engine=platform.git_engine,
            session_id=session_id,
            binding=binding,
            egress_allowed_cidrs=scope.governance.egress.allowed_cidrs,
            block_internal_subnets=scope.governance.egress.block_internal_subnets,
            fallback_user=record.tenant_user_id,
            fallback_org=record.tenant_org_id,
        )

    return ensure


def _github_read(scope: CallScope) -> Callable[..., Awaitable[GithubAccess]]:
    platform = scope.platform

    async def access(repository: str | None = None) -> GithubAccess:
        return await github_read_access(
            platform.git_engine, platform.db_mgr, scope.org_id, scope.git_bundle, repository
        )

    return access


def _github_write(scope: CallScope) -> Callable[[str], Awaitable[GithubAccess]]:
    platform = scope.platform

    async def access(repository: str) -> GithubAccess:
        return await github_write_access(
            repository,
            engine=platform.git_engine,
            db_mgr=platform.db_mgr,
            org=scope.org_id,
            publishes_as_member=scope.publishes_as_member,
            git_bundle=scope.git_bundle,
        )

    return access


def _repository_cloned(scope: CallScope) -> Callable[[str, str], Awaitable[str | None]]:
    """A clone's setup, dev container image and repository skills; unlike a turn's, no project
    memory is recalled (that is an embedding)."""
    platform, org = scope.platform, scope.org_id

    async def cloned(session_id: str, folder: str) -> str | None:
        await remember_active_repository(platform.store, session_id, org, folder)
        notes: list[str] = []
        sandbox_cfg = dict(scope.sandbox_settings)
        if sandbox_cfg.get("run_repo_setup"):
            setup = await run_repository_setup(
                platform.driver, session_id, folder, platform.event_bus
            )
            if setup:
                notes.append(setup)
        general = await effective_settings(platform.db_mgr, org, "general")
        notes.extend(
            await repository_extras(
                platform.db_mgr,
                platform.store,
                platform.driver,
                session_id,
                org,
                folder,
                sandbox_cfg,
                general,
            )
        )
        with contextlib.suppress(Exception):
            await sync_workspace_manifest(platform.driver, session_id, active_repo=folder)
        return "\n\n".join(notes) if notes else None

    return cloned


def _active_repository_chosen(scope: CallScope) -> Callable[[str, str], Awaitable[None]]:
    async def chosen(session_id: str, folder: str) -> None:
        await choose_active_repository(scope.platform.store, session_id, scope.org_id, folder)

    return chosen


def _agent_env_changed(
    scope: CallScope,
) -> Callable[[str, dict[str, str]], Awaitable[None]]:
    async def changed(session_id: str, env: dict[str, str]) -> None:
        await remember_agent_env(scope.platform.store, session_id, scope.org_id, env)

    return changed


def _branch_published(scope: CallScope) -> Callable[[str, str, str, str], Awaitable[None]]:
    async def published(session_id: str, repo: str, branch: str, sha: str) -> None:
        await scope.platform.store.mutate_session(
            session_id, scope.org_id, remember_pushed_head(repo, branch, sha)
        )

    return published


def _prepare_folder(
    scope: CallScope,
) -> Callable[[str, str], Awaitable[FolderSetupOutcome | None]]:
    platform = scope.platform

    async def prepare(session_id: str, folder: str) -> FolderSetupOutcome | None:
        setups = await RepositorySetupStore(platform.db_mgr).list(scope.org_id)
        if not setups:
            return None
        exec_ctx = current_execution_ctx.get()
        env = dict(exec_ctx.agent_env) if exec_ctx is not None else {}
        return await prepare_repository_folder(platform.driver, session_id, setups, folder, env)

    return prepare


def _processes_changed(scope: CallScope) -> Callable[[str], Awaitable[None]]:
    platform = scope.platform
    keepalive = float(scope.sandbox_settings["process_keepalive_minutes"]) * 60

    async def changed(session_id: str) -> None:
        await ProcessTracker.for_app(platform.db_mgr, platform.event_bus).refresh(
            session_id, scope.org_id, platform.driver, keepalive_seconds=keepalive
        )

    return changed
