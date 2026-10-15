"""The default flow, end to end: clone a repository, commit in it, publish with ``git_push``.

Nothing here is staged to suit the publisher: the repository comes from the real
``clone_repository`` tool (into ``/workspace/<name>``, not into the working directory), the
commit is a plain ``git commit`` in the sandbox, the policy is the platform's default
(``github_app`` signing, co-authored) for a member who linked GitHub while the organization has
no GitHub App installation mapped, and publishing goes through the registry like a turn's call.
"""

from __future__ import annotations

import asyncio
import pathlib
import subprocess

import httpx
import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.tools.registry import ToolRegistry
from git_engine import publish as publish_module
from git_engine.config import GitEngineSettings
from git_engine.engine import GitEngine

from specifications.interfaces.agent import GitPolicy, TurnPolicy
from tests.support.local_git_driver import LocalGitDriver
from tests.unit.test_branch_publisher import SimulatedGitHub, git


@pytest.fixture
async def sandbox(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    # "GitHub" as the platform publishes to it: <git_base_url>/acme/widgets.git.
    bare = tmp_path / "remotes" / "acme" / "widgets.git"
    bare.parent.mkdir(parents=True)
    await asyncio.to_thread(
        subprocess.run, ["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True
    )
    git(bare, "config", "uploadpack.allowFilter", "true")
    git(bare, "config", "uploadpack.allowAnySHA1InWant", "true")
    seed = tmp_path / "seed"
    await asyncio.to_thread(
        subprocess.run, ["git", "clone", "-q", str(bare), str(seed)], check=True
    )
    git(seed, "config", "user.name", "Seed")
    git(seed, "config", "user.email", "seed@example.com")
    (seed / "README.md").write_text("hello\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "Initial commit")
    git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(seed, "push", "-q", "origin", "HEAD:refs/heads/feature/existing")

    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        f'[url "{bare}"]\n\tinsteadOf = https://github.com/acme/widgets\n'
        f'[url "{bare}"]\n\tinsteadOf = https://github.com/acme/widgets.git\n'
        "[user]\n\tname = octocat\n\temail = octo@example.com\n"
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    driver = LocalGitDriver(workspace, tmp_path / "shim")
    driver.home = home

    github = SimulatedGitHub(bare, tmp_path / "scratch")
    real = httpx.AsyncClient
    monkeypatch.setattr(
        publish_module.httpx,
        "AsyncClient",
        lambda **kwargs: real(transport=httpx.MockTransport(github.handler), **kwargs),
    )
    return driver, github, bare, workspace


async def test_clone_commit_then_git_push_publishes_with_the_members_account_when_no_app_is_mapped(
    sandbox: tuple[LocalGitDriver, SimulatedGitHub, pathlib.Path, pathlib.Path],
) -> None:
    driver, github, bare, workspace = sandbox
    registry = ToolRegistry(driver=driver, session_id="s1")  # type: ignore[arg-type]
    registry.bind_git_engine(
        GitEngine(
            driver=driver,
            settings=GitEngineSettings(git_base_url=f"file://{bare.parent.parent}"),
        )
    )  # type: ignore[arg-type]
    policy = GitPolicy(
        signing_mode="github_app",
        authorship="co_authored",
        user_token="gho_member",
        app_installations=(),
    )
    token = current_execution_ctx.set(
        ExecutionContext(
            session_id="s1",
            event_queue=asyncio.Queue(),
            parent_model="m",
            policy=TurnPolicy(git=policy),
        )
    )
    try:
        cloned = await registry.call_tool("clone_repository", {"repository": "acme/widgets"})
        assert "Cloned acme/widgets into" in str(cloned), cloned
        assert (workspace / "widgets" / ".git").exists(), "cloned into a subfolder, as it always is"

        made = await driver.exec_command(
            "s1",
            "cd /workspace/widgets && git checkout -q -b feat/rocket-x && echo hi > a.txt"
            " && git add -A && git commit -q -m 'feat: add a'",
        )
        assert made.exit_code == 0, made.stderr

        result = str(await registry.call_tool("git_push", {}))
    finally:
        current_execution_ctx.reset(token)

    assert result.startswith("Published 1 commit(s) to acme/widgets@feat/rocket-x"), result
    assert "Published with your GitHub account" in result
    assert set(github.tokens) == {"Bearer gho_member"}
    assert git(bare, "log", "-1", "--format=%an|%s", "refs/heads/feat/rocket-x") == (
        "widgets-app[bot]|feat: add a"
    )


async def test_a_clone_of_an_existing_remote_branch_gets_the_new_commit_appended(
    sandbox: tuple[LocalGitDriver, SimulatedGitHub, pathlib.Path, pathlib.Path],
) -> None:
    driver, _github, bare, _ = sandbox
    registry = ToolRegistry(driver=driver, session_id="s1")  # type: ignore[arg-type]
    registry.bind_git_engine(
        GitEngine(
            driver=driver,
            settings=GitEngineSettings(git_base_url=f"file://{bare.parent.parent}"),
        )  # type: ignore[arg-type]
    )
    token = current_execution_ctx.set(
        ExecutionContext(
            session_id="s1",
            event_queue=asyncio.Queue(),
            parent_model="m",
            policy=TurnPolicy(git=GitPolicy(user_token="gho_member")),
        )
    )
    try:
        await registry.call_tool(
            "clone_repository", {"repository": "acme/widgets", "branch": "feature/existing"}
        )
        made = await driver.exec_command(
            "s1",
            "cd /workspace/widgets && echo hi > a.txt && git add -A && git commit -q -m 'chore: test'",
        )
        assert made.exit_code == 0, made.stderr

        result = str(await registry.call_tool("git_push", {"branch": "feature/existing"}))
    finally:
        current_execution_ctx.reset(token)

    assert result.startswith("Published 1 commit(s) to acme/widgets@feature/existing"), result
    assert git(bare, "log", "-1", "--format=%s", "refs/heads/feature/existing") == "chore: test"
    assert git(bare, "rev-list", "--count", "refs/heads/main..refs/heads/feature/existing") == "1"
