"""Git: status, diffs, history, commits, branches, checkpoints, reverts and pull requests."""

import logging
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field
from sandbox_driver.workspace import RepositoryNotFoundError

from agent_core.tools import git_tools, tier2

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

PATH_DESCRIPTION = (
    "Repository folder, e.g. 'my-app' for /workspace/my-app. Default: the session's active "
    "repository (the first one cloned), or the only repository under /workspace."
)
RepositoryPath = Annotated[str | None, Field(description=PATH_DESCRIPTION)]


def register(registry: "ToolRegistry") -> None:
    _register_git_inspection_tools(registry)
    _register_git_change_tools(registry)
    _register_git_publish_tools(registry)


async def _repository(
    registry: "ToolRegistry", tool: str, path: str | None
) -> tuple[str, str] | str:
    """``(session_id, repository folder)`` for a git tool, or the error text for the agent."""
    target = registry._sandbox_target(tool)
    if not target:
        return f"Error: Sandbox not initialized for {tool}"
    _, session_id = target
    await registry._ensure_sandbox(session_id)
    try:
        return session_id, await registry.repository_dir(session_id, path)
    except RepositoryNotFoundError as err:
        return f"Error: {err}"


def _register_git_inspection_tools(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Return the porcelain git status (current branch + changed/untracked files)"
            " of a repository."
        )
    )
    async def git_status(path: RepositoryPath = None) -> str:
        resolved = await _repository(registry, "git_status", path)
        if isinstance(resolved, str):
            return resolved
        session_id, folder = resolved
        assert registry._driver is not None
        return f"[{folder}]\n" + await tier2.git_status(registry._driver, session_id, folder)

    @registry._tool(
        description=(
            "Show the current unified git diff (optionally staged, scoped to a file, or stat-only)."
        )
    )
    async def git_diff(
        file: Annotated[
            str | None,
            Field(description="Optional file, relative to the repository, to scope the diff to."),
        ] = None,
        staged: Annotated[
            bool, Field(description="Show staged (cached) changes instead (default: false).")
        ] = False,
        stat_only: Annotated[
            bool, Field(description="Return only a diffstat summary (default: false).")
        ] = False,
        path: RepositoryPath = None,
    ) -> str:
        resolved = await _repository(registry, "git_diff", path)
        if isinstance(resolved, str):
            return resolved
        session_id, folder = resolved
        assert registry._driver is not None
        return await tier2.git_diff(registry._driver, session_id, file, staged, stat_only, folder)

    @registry._tool(
        description=(
            "Return structured git history: author, date, subject and changed files per commit."
        )
    )
    async def git_log(
        max_count: Annotated[
            int, Field(description="Maximum commits to return (default 20, max 100).")
        ] = 20,
        file: Annotated[
            str | None, Field(description="Optional file or folder to scope the history to.")
        ] = None,
        author: Annotated[str | None, Field(description="Optional author filter.")] = None,
        path: RepositoryPath = None,
    ) -> str:
        resolved = await _repository(registry, "git_log", path)
        if isinstance(resolved, str):
            return resolved
        session_id, folder = resolved
        assert registry._driver is not None
        return await tier2.git_log(registry._driver, session_id, max_count, file, author, folder)


def _register_git_change_tools(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Commit every change in a repository, following the organization's authorship and"
            " signing policy (co-author trailers included). Prefer a checkpoint first for"
            " destructive work."
        )
    )
    async def git_commit(
        message: Annotated[str, Field(description="Commit message.")],
        branch: Annotated[
            str | None,
            Field(description="Optional branch to create/checkout before committing."),
        ] = None,
        co_authors: Annotated[
            list[str] | None, Field(description="Optional co-author names to credit.")
        ] = None,
        path: RepositoryPath = None,
        amend: Annotated[
            bool,
            Field(description="Fold into the last commit (push with force_with_lease)."),
        ] = False,
    ) -> str:
        resolved = await _repository(registry, "git_commit", path)
        if isinstance(resolved, str):
            return resolved
        session_id, folder = resolved
        assert registry._driver is not None
        return await git_tools.git_commit(
            registry._driver,
            session_id,
            message,
            branch,
            co_authors,
            registry._git_engine,
            policy=registry._git_policy(),
            workdir=folder,
            amend=amend,
        )

    @registry._tool(description="Create and switch to a new git branch.")
    async def git_branch(
        name: Annotated[str, Field(description="New branch name.")],
        from_ref: Annotated[
            str | None, Field(description="Optional base commit/branch to branch from.")
        ] = None,
        path: RepositoryPath = None,
    ) -> str:
        resolved = await _repository(registry, "git_branch", path)
        if isinstance(resolved, str):
            return resolved
        session_id, folder = resolved
        assert registry._driver is not None
        return await git_tools.git_branch(registry._driver, session_id, name, from_ref, folder)

    @registry._tool(
        description=(
            "Snapshot a repository, uncommitted work included, as a restorable checkpoint. "
            "Cheap undo point before large or risky edits."
        )
    )
    async def git_checkpoint(
        label: Annotated[
            str | None, Field(description="Optional human-readable label suffix.")
        ] = None,
        path: RepositoryPath = None,
    ) -> str:
        resolved = await _repository(registry, "git_checkpoint", path)
        if isinstance(resolved, str):
            return resolved
        session_id, folder = resolved
        assert registry._driver is not None
        return await git_tools.git_checkpoint(registry._driver, session_id, label, folder)

    @registry._tool(
        description=(
            "Restore a repository to a checkpoint or commit. mode='files' restores "
            "tracked files safely; mode='hard' discards all changes and requires "
            "approval."
        )
    )
    async def git_revert(
        reference: Annotated[
            str, Field(description="Checkpoint tag, commit SHA, or ref to restore.")
        ],
        mode: Annotated[
            Literal["files", "hard"],
            Field(description="Restore tracked files (default) or hard reset."),
        ] = "files",
        path: RepositoryPath = None,
    ) -> str:
        resolved = await _repository(registry, "git_revert", path)
        if isinstance(resolved, str):
            return resolved
        session_id, folder = resolved
        approve = registry._turn_callback("approval")
        if mode == "hard" and approve:
            decision = await approve(
                "git_revert_hard",
                f"Destructively reset {folder} to {reference} (discards uncommitted work).",
                "critical",
            )
            if not decision.startswith("Approved"):
                return f"Hard revert not approved: {decision}"
        assert registry._driver is not None
        return await git_tools.git_revert(registry._driver, session_id, reference, mode, folder)


def _register_git_publish_tools(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Publish the current branch to GitHub: the only way commits leave the sandbox (a raw"
            " `git push` is refused). Depending on the organization's signing mode the commits"
            " are re-created through GitHub's API so GitHub signs them (local commit hashes then"
            " change), or pushed as they are. Commit first; call this before git_open_pr if you"
            " only want to publish."
        )
    )
    async def git_push(
        branch: Annotated[
            str | None, Field(description="Branch to publish (default: the checked-out one).")
        ] = None,
        force_with_lease: Annotated[
            bool,
            Field(description="Allow replacing remote commits you have not fetched (rarely)."),
        ] = False,
        path: RepositoryPath = None,
    ) -> str:
        resolved = await _repository(registry, "git_push", path)
        if isinstance(resolved, str):
            return resolved
        session_id, folder = resolved
        return await git_tools.git_push(
            session_id,
            branch,
            force_with_lease,
            registry._git_engine,
            policy=registry._git_policy(),
            path=folder,
            published=registry._turn_callback("branch_published"),
        )

    @registry._tool(
        description="Publish the current branch and open a pull request for it on GitHub."
    )
    async def git_open_pr(
        title: Annotated[str, Field(description="Pull request title.")],
        body: Annotated[str, Field(description="Pull request description.")],
        target_branch: Annotated[
            str | None, Field(description="Base branch to merge into (default: main).")
        ] = None,
        path: RepositoryPath = None,
    ) -> str:
        resolved = await _repository(registry, "git_open_pr", path)
        if isinstance(resolved, str):
            return resolved
        session_id, folder = resolved
        assert registry._driver is not None
        result = await git_tools.git_open_pr(
            registry._driver,
            session_id,
            title,
            body,
            target_branch,
            registry._git_engine,
            policy=registry._git_policy(),
            path=folder,
        )
        follow = registry._turn_callback("github_watch")
        if follow and (pull := git_tools.parse_pull_request_url(result)):
            repository, number = pull
            await follow(repository, number, None, True)
            result += "\nThis session now follows the pull request: CI, reviews and comments will reach you."
        return result
