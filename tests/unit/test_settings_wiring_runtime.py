"""Sandbox, GitHub and MCP settings change what the sandbox, the git tools and the servers do."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from agent_core.tools.git_tools import commit_spec_for_policy, git_open_pr
from api.services.git_credentials import resolve_session_git_credentials
from api.services.sandbox_catalogue import SandboxCatalogue
from api.services.settings import save_org_config
from api.store import InMemorySessionStore
from api.turns import callbacks as callbacks_module
from api.turns.callbacks import build_turn_callbacks
from api.turns.provisioning import provision_session_sandbox
from api.turns.shared import _turn_service
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import SandboxStatus

# --- sandboxes: what a new workspace is made of ------------------------------------------


async def _provision(org: str, **binding: Any) -> Any:
    """Provision a session's sandbox against a recording driver; returns the driver."""
    driver = FakeSandboxDriver()
    driver.ensure_workspace = AsyncMock(wraps=driver.ensure_workspace)  # type: ignore[method-assign]
    await provision_session_sandbox(
        store=InMemorySessionStore(),
        driver=driver,
        event_bus=AsyncMock(),
        db_mgr=None,
        git_engine=None,
        session_id=f"session_{org}",
        binding=SimpleNamespace(
            **{"sandbox_image": "", "cpu_limit": "", "memory_limit": "", **binding}
        ),
        egress_allowed_cidrs=[],
        block_internal_subnets=False,
        fallback_user="u1",
        fallback_org=org,
    )
    return driver


def _workspace(driver: Any) -> Any:
    return driver.ensure_workspace.call_args.args[0]


@pytest.mark.setting("sandboxes.cpu_limit")
@pytest.mark.setting("sandboxes.memory_limit")
async def test_the_organization_limits_size_the_sandbox_unless_the_agent_sets_its_own() -> None:
    org = "wire_limits"
    await save_org_config(None, org, "sandboxes", {"cpu_limit": 3.0, "memory_limit": "6Gi"}, [])

    workspace = _workspace(await _provision(org))
    assert (workspace.resources.cpu_cores, workspace.resources.memory_limit) == (3.0, "6Gi")

    own = _workspace(await _provision(org, cpu_limit="1.5", memory_limit="2Gi"))
    assert (own.resources.cpu_cores, own.resources.memory_limit) == (1.5, "2Gi")


@pytest.mark.setting("sandboxes.require_non_root")
async def test_the_non_root_requirement_is_carried_to_the_workspace() -> None:
    org = "wire_non_root"
    assert _workspace(await _provision(org)).require_non_root is False

    await save_org_config(None, org, "sandboxes", {"require_non_root": True}, [])
    assert _workspace(await _provision(org)).require_non_root is True


@pytest.mark.setting("sandboxes.default_image")
async def test_the_default_image_seeds_the_organizations_default_sandbox() -> None:
    org = "wire_default_image"
    await save_org_config(None, org, "sandboxes", {"default_image": "registry.example/box:7"}, [])

    assert (await SandboxCatalogue(None).default(org)).image == "registry.example/box:7"
    assert _workspace(await _provision(org)).container_image == "registry.example/box:7"


@pytest.mark.setting("sandboxes.allowed_images")
async def test_an_image_outside_the_catalogue_and_the_allowed_patterns_is_refused() -> None:
    org = "wire_allowed_images"
    with pytest.raises(ValueError, match="not in the organization's sandbox catalogue"):
        await _provision(org, sandbox_image="rogue.example/box:1")

    await save_org_config(None, org, "sandboxes", {"allowed_images": ["rogue.example/*"]}, [])
    assert _workspace(
        await _provision(org, sandbox_image="rogue.example/box:1")
    ).container_image == ("rogue.example/box:1")


# --- sandboxes: what the turn's tools are allowed to do ---------------------------------


def _turn(org: str, driver: Any = None) -> Any:
    return SimpleNamespace(
        session_id=f"session_{org}",
        req=SimpleNamespace(
            store=InMemorySessionStore(),
            event_bus=AsyncMock(),
            db_mgr=None,
            driver=driver or AsyncMock(),
            git_engine=None,
        ),
        session=SimpleNamespace(tenant_org_id=org, tenant_user_id="u1"),
        binding=SimpleNamespace(
            knowledge_base_ids=(), skill_ids=(), cpu_limit="", memory_limit="", sandbox_image=""
        ),
        governance=SimpleNamespace(
            egress=SimpleNamespace(allowed_cidrs=[], block_internal_subnets=False)
        ),
    )


def _bundle() -> Any:
    return SimpleNamespace(token=None)


@pytest.mark.setting("sandboxes.max_background_processes")
async def test_the_background_process_limit_reaches_the_agents_tools() -> None:
    org = "wire_process_limit"
    assert (await build_turn_callbacks(_turn(org), _bundle(), None)).max_background_processes == 5

    await save_org_config(None, org, "sandboxes", {"max_background_processes": 2}, [])
    assert (await build_turn_callbacks(_turn(org), _bundle(), None)).max_background_processes == 2


@pytest.mark.setting("sandboxes.process_keepalive_minutes")
async def test_the_keepalive_window_is_passed_to_process_tracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = "wire_keepalive"
    await save_org_config(None, org, "sandboxes", {"process_keepalive_minutes": 7}, [])
    tracker = AsyncMock()
    monkeypatch.setattr(
        callbacks_module.ProcessTracker, "for_app", staticmethod(lambda *_: tracker)
    )

    callbacks = await build_turn_callbacks(_turn(org), _bundle(), None)
    await callbacks.processes_changed("s1")

    assert tracker.refresh.call_args.kwargs["keepalive_seconds"] == 7 * 60


@pytest.mark.setting("sandboxes.run_repo_setup")
async def test_a_cloned_repositorys_setup_runs_only_when_the_organization_allows_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = "wire_repo_setup"
    setup = AsyncMock(return_value="ran")
    monkeypatch.setattr(callbacks_module, "run_repository_setup", setup)

    enabled = await build_turn_callbacks(_turn(org), _bundle(), None)
    assert await enabled.repository_cloned("s1", "repo") == "ran"

    await save_org_config(None, org, "sandboxes", {"run_repo_setup": False}, [])
    disabled = await build_turn_callbacks(_turn(org), _bundle(), None)
    assert await disabled.repository_cloned("s1", "repo") is None
    assert setup.await_count == 1


# --- github: commits, pull requests and the App token ------------------------------------


async def _git_policy(org: str, **member: Any) -> Any:
    return (
        await _turn_service.plan_turn(
            db_mgr=None, org_id=org, user_id="u1", agent_id=None, global_default_model="p/m"
        )
    ).policy.git


@pytest.mark.setting("github.commit_authorship_policy")
@pytest.mark.setting("github.bot_author_name")
@pytest.mark.setting("github.bot_author_email")
async def test_the_authorship_policy_and_bot_identity_decide_who_a_commit_is_by() -> None:
    org = "wire_authorship"
    await save_org_config(
        None,
        org,
        "github",
        {
            "commit_authorship_policy": "bot_only",
            "bot_author_name": "Release Bot",
            "bot_author_email": "bot@corp.example",
        },
        [],
    )

    spec = commit_spec_for_policy("msg", None, None, await _git_policy(org))

    assert (spec.committer_name, spec.committer_email) == ("Release Bot", "bot@corp.example")
    assert spec.co_authors == []


@pytest.mark.setting("github.commit_signing_mode")
async def test_the_signing_mode_reaches_the_commit() -> None:
    org = "wire_signing"
    await save_org_config(None, org, "github", {"commit_signing_mode": "none"}, [])

    spec = commit_spec_for_policy("msg", None, None, await _git_policy(org))

    assert spec.signing_mode.value == "none"


class _PullRequests:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def push_and_open_pr(
        self,
        session_id: str,
        title: str,
        body: str,
        target: str,
        token: str | None = None,
        publish: object = None,
        **_: Any,
    ) -> str:
        self.calls.append((target, token))
        return "https://github.com/o/r/pull/1"


@pytest.mark.setting("github.pr_creation_policy")
@pytest.mark.setting("github.default_branch")
async def test_pull_requests_use_the_policys_credential_and_base_branch() -> None:
    org = "wire_pr"
    await save_org_config(
        None, org, "github", {"pr_creation_policy": "as_bot", "default_branch": "develop"}, []
    )
    policy = await _git_policy(org)
    policy = type(policy)(**{**policy.__dict__, "user_token": "member-token"})
    engine = _PullRequests()

    await git_open_pr(AsyncMock(), "s", "t", "b", git_engine=engine, policy=policy)  # type: ignore[arg-type]

    assert engine.calls == [("develop", None)]  # as_bot: never the member's token


@pytest.mark.setting("github.app_token_permissions")
@pytest.mark.setting("github.app_token_target_repositories")
async def test_the_app_token_is_requested_with_the_configured_scope() -> None:
    org = "wire_app_token"
    await save_org_config(
        None,
        org,
        "github",
        {
            "app_token_permissions": {"contents": "write", "pull_requests": "write"},
            "app_token_target_repositories": "acme/api, acme/web",
        },
        [],
    )
    engine = AsyncMock()
    engine.get_github_access_token.return_value = "app-token"
    engine.settings = SimpleNamespace(bot_author_name="Bot", bot_author_email="bot@x.io")

    bundle = await resolve_session_git_credentials(
        user_id="u1", org_id=org, git_engine=engine, db_mgr=None, user_overrides={}, repo=None
    )

    assert bundle.token == "app-token"
    kwargs = engine.get_github_access_token.call_args.kwargs
    assert kwargs["permissions"] == {"contents": "write", "pull_requests": "write"}
    assert kwargs["repositories"] == ["api", "web"]


# --- sandboxes: the running-sandbox cap ---------------------------------------------------


@pytest.mark.setting("sandboxes.max_running_sandboxes")
async def test_an_organization_at_its_running_sandbox_cap_cannot_start_another() -> None:
    from api.services.sandbox_quota import SandboxQuotaExceededError

    from specifications.interfaces.session import SessionRecord

    org = "wire_running_cap"
    await save_org_config(None, org, "sandboxes", {"max_running_sandboxes": 1}, [])
    store, driver = InMemorySessionStore(), FakeSandboxDriver()
    await store.create_session(
        SessionRecord(
            session_id="already_running",
            tenant_org_id=org,
            tenant_user_id="u1",
            title="t",
            sandbox_status=SandboxStatus.RUNNING,
        )
    )
    await driver.start_sandbox("already_running")

    with pytest.raises(SandboxQuotaExceededError):
        await provision_session_sandbox(
            store=store,
            driver=driver,
            event_bus=AsyncMock(),
            db_mgr=None,
            git_engine=None,
            session_id="one_too_many",
            binding=SimpleNamespace(sandbox_image="", cpu_limit="", memory_limit=""),
            egress_allowed_cidrs=[],
            block_internal_subnets=False,
            fallback_user="u1",
            fallback_org=org,
        )
