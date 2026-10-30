"""Git, test and shell tools act on the cloned repository, not on /workspace itself.

``clone_repository`` puts code in ``/workspace/<name>``. These tests run real git in a temporary
directory standing for ``/workspace`` (``LocalGitDriver``) so the resolution, the commit and the
publish are exercised against real repositories.
"""

from __future__ import annotations

import asyncio
import pathlib
import subprocess
from collections.abc import Iterator

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.tools.builtin.commands import fold_leading_cd
from agent_core.tools.registry import ToolRegistry
from git_engine.config import GitEngineSettings
from git_engine.engine import GitEngine
from sandbox_driver.workspace import (
    RepositoryNotFoundError,
    resolve_project_dir,
    resolve_repository_dir,
)

from specifications.interfaces.agent import GitPolicy, TurnPolicy
from specifications.interfaces.git import PublishSpec, SigningMode
from tests.support.local_git_driver import LocalGitDriver

SLUG = "acme/widgets"


def git(cwd: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class Workspace:
    """A /workspace with a bare 'GitHub' remote that `https://github.com/acme/*` maps to."""

    def __init__(self, tmp: pathlib.Path) -> None:
        self.root = tmp / "workspace"
        self.root.mkdir()
        self.remotes = tmp / "remotes"
        self.home = tmp / "home"
        self.home.mkdir()
        self.driver = LocalGitDriver(self.root, tmp / "shim")
        self.driver.home = self.home
        git(self.home, "init", "-q")  # any repository, only to run `git config --global`
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = Rocket Test\n\temail = test@rocket.test\n"
            f'[url "{self.remotes}/acme/"]\n\tinsteadOf = https://github.com/acme/\n'
        )

    def remote(self, name: str) -> pathlib.Path:
        bare = self.remotes / "acme" / f"{name}.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        # Served as GitHub serves them to the platform's publisher: partial, by commit.
        git(bare, "config", "uploadpack.allowFilter", "true")
        git(bare, "config", "uploadpack.allowAnySHA1InWant", "true")
        seed = self.remotes / f"{name}-seed"
        subprocess.run(
            ["git", "clone", "-q", str(bare), str(seed)], check=True, capture_output=True
        )
        git(
            seed,
            "-c",
            "user.name=Seed",
            "-c",
            "user.email=s@x",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "Initial",
        )
        (seed / "README.md").write_text(f"# {name}\n")
        git(seed, "add", "-A")
        git(seed, "-c", "user.name=Seed", "-c", "user.email=s@x", "commit", "-q", "-m", "Readme")
        git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")
        return bare

    def clone(self, name: str) -> pathlib.Path:
        self.remote(name)
        target = self.root / name
        subprocess.run(
            ["git", "clone", "-q", f"https://github.com/acme/{name}", str(target)],
            check=True,
            env={"HOME": str(self.home), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
        )
        return target


@pytest.fixture
def workspace(tmp_path: pathlib.Path) -> Workspace:
    return Workspace(tmp_path)


@pytest.fixture
def turn() -> Iterator[ExecutionContext]:
    ctx = ExecutionContext(session_id="s1", event_queue=asyncio.Queue(), parent_model="m")
    token = current_execution_ctx.set(ctx)
    yield ctx
    current_execution_ctx.reset(token)


# -- the resolver ---------------------------------------------------------------------------


async def test_the_only_clone_under_the_workspace_is_found(workspace: Workspace) -> None:
    workspace.clone("widgets")

    assert await resolve_repository_dir(workspace.driver, "s1") == "/workspace/widgets"


async def test_several_clones_and_no_choice_names_them(workspace: Workspace) -> None:
    workspace.clone("widgets")
    workspace.clone("gadgets")

    with pytest.raises(RepositoryNotFoundError, match="/workspace/gadgets, /workspace/widgets"):
        await resolve_repository_dir(workspace.driver, "s1")
    with pytest.raises(RepositoryNotFoundError, match="several"):
        await resolve_project_dir(workspace.driver, "s1")


async def test_the_named_folder_wins_then_the_active_repository(workspace: Workspace) -> None:
    workspace.clone("widgets")
    workspace.clone("gadgets")

    assert await resolve_repository_dir(workspace.driver, "s1", "gadgets") == "/workspace/gadgets"
    assert (
        await resolve_repository_dir(workspace.driver, "s1", active="/workspace/widgets")
        == "/workspace/widgets"
    )
    with pytest.raises(RepositoryNotFoundError, match="/workspace/nope is not a git repository"):
        await resolve_repository_dir(workspace.driver, "s1", "nope")


async def test_a_workspace_without_repositories(workspace: Workspace) -> None:
    with pytest.raises(RepositoryNotFoundError, match="clone one first"):
        await resolve_repository_dir(workspace.driver, "s1")
    assert await resolve_project_dir(workspace.driver, "s1") == "/workspace"
    assert await resolve_project_dir(workspace.driver, "s1", "scratch/app") == (
        "/workspace/scratch/app"
    )


async def test_a_stale_active_repository_falls_back_to_discovery(workspace: Workspace) -> None:
    workspace.clone("widgets")

    assert (
        await resolve_repository_dir(workspace.driver, "s1", active="/workspace/deleted")
        == "/workspace/widgets"
    )


# -- the tools ------------------------------------------------------------------------------


async def test_git_commit_commits_in_the_clone_and_never_creates_a_workspace_repository(
    workspace: Workspace, turn: ExecutionContext
) -> None:
    clone = workspace.clone("widgets")
    (clone / "feature.txt").write_text("new\n")
    registry = ToolRegistry(driver=workspace.driver, session_id="s1")  # type: ignore[arg-type]
    registry.bind_git_engine(GitEngine(driver=workspace.driver))  # type: ignore[arg-type]

    result = await registry.call_tool("git_commit", {"message": "Add feature", "branch": "feat/x"})

    assert "SHA:" in result, result
    assert not (workspace.root / ".git").exists(), "no repository is created at /workspace"
    assert git(clone, "log", "-1", "--format=%s") == "Add feature"
    assert git(clone, "branch", "--show-current") == "feat/x"
    assert git(clone, "status", "--porcelain") == ""


async def test_git_status_reports_which_repository_it_read(
    workspace: Workspace, turn: ExecutionContext
) -> None:
    clone = workspace.clone("widgets")
    (clone / "dirty.txt").write_text("x\n")
    registry = ToolRegistry(driver=workspace.driver, session_id="s1")  # type: ignore[arg-type]

    status = await registry.call_tool("git_status", {})

    assert status.startswith("[/workspace/widgets]") and "dirty.txt" in status


async def test_git_tools_refuse_to_guess_between_two_clones(
    workspace: Workspace, turn: ExecutionContext
) -> None:
    workspace.clone("widgets")
    workspace.clone("gadgets")
    registry = ToolRegistry(driver=workspace.driver, session_id="s1")  # type: ignore[arg-type]

    refused = await registry.call_tool("git_status", {})
    chosen = await registry.call_tool("git_status", {"path": "gadgets"})

    assert refused.startswith("Error: There are several repositories")
    assert chosen.startswith("[/workspace/gadgets]")


async def test_the_active_repository_is_the_default_and_bash_runs_where_it_is_told(
    workspace: Workspace, turn: ExecutionContext
) -> None:
    workspace.clone("widgets")
    workspace.clone("gadgets")
    turn.active_repository = "/workspace/gadgets"
    registry = ToolRegistry(driver=workspace.driver, session_id="s1")  # type: ignore[arg-type]

    status = await registry.call_tool("git_status", {})
    here = await registry.call_tool(
        "bash_exec", {"command": "basename $(pwd)", "cwd": "/workspace/gadgets"}
    )
    elsewhere = await registry.call_tool(
        "bash_exec", {"command": "basename $(pwd)", "cwd": "widgets"}
    )

    assert status.startswith("[/workspace/gadgets]")
    assert here.strip() == "gadgets" and elsewhere.strip() == "widgets"


async def test_bash_needs_a_folder_and_a_leading_cd_becomes_that_folder(
    workspace: Workspace, turn: ExecutionContext
) -> None:
    """Agents wrote `cd <repo> && ...` into every command: the folder is a required argument,
    and a leading `cd` still written is run as that folder instead."""
    workspace.clone("widgets")
    registry = ToolRegistry(driver=workspace.driver, session_id="s1")  # type: ignore[arg-type]

    with pytest.raises(Exception, match="cwd"):
        await registry.call_tool("bash_exec", {"command": "pwd"})
    folded = await registry.call_tool(
        "bash_exec", {"command": "cd widgets && pwd", "cwd": "/workspace"}
    )

    assert folded.strip() == "/workspace/widgets"
    assert fold_leading_cd("cd /x/y && make test", "/workspace") == ("make test", "/x/y")
    assert fold_leading_cd('cd "$HOME" && ls', "/workspace") == ('cd "$HOME" && ls', "/workspace")


async def test_the_first_clone_becomes_the_active_repository(
    workspace: Workspace, turn: ExecutionContext
) -> None:
    for name in ("widgets", "gadgets"):
        workspace.remote(name)
    registry = ToolRegistry(driver=workspace.driver, session_id="s1")  # type: ignore[arg-type]

    first = await registry.call_tool("clone_repository", {"repository": "acme/widgets"})
    second = await registry.call_tool("clone_repository", {"repository": "acme/gadgets"})

    assert "now the active repository" in first and "active repository" not in second
    assert turn.active_repository == "/workspace/widgets"
    assert (await registry.call_tool("git_status", {})).startswith("[/workspace/widgets]")


async def test_a_commit_in_a_subfolder_clone_is_published_from_that_clone(
    workspace: Workspace, turn: ExecutionContext
) -> None:
    clone = workspace.clone("widgets")
    turn.policy = TurnPolicy(git=GitPolicy(signing="none", user_token="gho_member"))
    registry = ToolRegistry(driver=workspace.driver, session_id="s1")  # type: ignore[arg-type]
    engine = GitEngine(
        driver=workspace.driver,  # type: ignore[arg-type]
        settings=GitEngineSettings(git_base_url=f"file://{workspace.remotes}"),
    )
    registry.bind_git_engine(engine)
    (clone / "feature.txt").write_text("new\n")

    await registry.call_tool("git_commit", {"message": "Add feature", "branch": "feat/sub"})
    published = await engine.publish_branch(
        "s1", PublishSpec(signing_mode=SigningMode.NONE, token="gho_member")
    )

    assert published.repository == SLUG and published.branch == "feat/sub"
    bare = workspace.remotes / "acme" / "widgets.git"
    assert git(bare, "log", "-1", "--format=%s", "refs/heads/feat/sub") == "Add feature"
