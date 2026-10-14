"""Git: status, diffs, history, commits, branches, checkpoints, reverts and pull requests."""

import logging
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from agent_core.context import current_execution_ctx
from agent_core.tools import git_tools, tier2

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register(registry: "ToolRegistry") -> None:
    _register_git_inspection_tools(registry)
    _register_git_change_tools(registry)
    _register_git_publish_tools(registry)


def _register_git_inspection_tools(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Return the porcelain git status (current branch + changed/untracked files)"
            " of the workspace."
        )
    )
    async def git_status() -> str:
        ctx = current_execution_ctx.get()
        s_id = ctx.session_id if ctx else registry._session_id
        if not registry._driver or not s_id:
            return "Error: Sandbox not initialized for git_status"
        await registry._ensure_sandbox(s_id)
        return await tier2.git_status(registry._driver, s_id)

    @registry._tool(
        description=(
            "Show the current unified git diff (optionally staged, scoped to a path, or stat-only)."
        )
    )
    async def git_diff(
        path: Annotated[
            str | None, Field(description="Optional file path to scope the diff to.")
        ] = None,
        staged: Annotated[
            bool, Field(description="Show staged (cached) changes instead (default: false).")
        ] = False,
        stat_only: Annotated[
            bool, Field(description="Return only a diffstat summary (default: false).")
        ] = False,
    ) -> str:
        ctx = current_execution_ctx.get()
        s_id = ctx.session_id if ctx else registry._session_id
        if not registry._driver or not s_id:
            return "Error: Sandbox not initialized for git_diff"
        await registry._ensure_sandbox(s_id)
        return await tier2.git_diff(registry._driver, s_id, path, staged, stat_only)

    @registry._tool(
        description=(
            "Return structured git history: author, date, subject and changed files per commit."
        )
    )
    async def git_log(
        max_count: Annotated[
            int, Field(description="Maximum commits to return (default 20, max 100).")
        ] = 20,
        path: Annotated[
            str | None, Field(description="Optional path to scope the history to.")
        ] = None,
        author: Annotated[str | None, Field(description="Optional author filter.")] = None,
    ) -> str:
        target = registry._sandbox_target("git_log")
        if not target:
            return "Error: Sandbox not initialized for git_log"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        return await tier2.git_log(driver, session_id, max_count, path, author)


def _register_git_change_tools(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Create a git commit (signed with co-author trailers when the GitHub App is"
            " configured). Prefer a checkpoint first for destructive work."
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
    ) -> str:
        target = registry._sandbox_target("git_commit")
        if not target:
            return "Error: Sandbox not initialized for git_commit"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        return await git_tools.git_commit(
            driver,
            session_id,
            message,
            branch,
            co_authors,
            registry._git_engine,
            policy=registry._git_policy(),
        )

    @registry._tool(description="Create and switch to a new git branch.")
    async def git_branch(
        name: Annotated[str, Field(description="New branch name.")],
        from_ref: Annotated[
            str | None, Field(description="Optional base commit/branch to branch from.")
        ] = None,
    ) -> str:
        target = registry._sandbox_target("git_branch")
        if not target:
            return "Error: Sandbox not initialized for git_branch"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        return await git_tools.git_branch(driver, session_id, name, from_ref)

    @registry._tool(
        description=(
            "Tag the current HEAD as a restorable checkpoint. Cheap undo point before "
            "large or risky edits."
        )
    )
    async def git_checkpoint(
        label: Annotated[
            str | None, Field(description="Optional human-readable label suffix.")
        ] = None,
    ) -> str:
        target = registry._sandbox_target("git_checkpoint")
        if not target:
            return "Error: Sandbox not initialized for git_checkpoint"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        return await git_tools.git_checkpoint(driver, session_id, label)

    @registry._tool(
        description=(
            "Restore the workspace to a checkpoint or commit. mode='files' restores "
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
    ) -> str:
        target = registry._sandbox_target("git_revert")
        if not target:
            return "Error: Sandbox not initialized for git_revert"
        approve = registry._turn_callback("approval")
        if mode == "hard" and approve:
            decision = await approve(
                "git_revert_hard",
                f"Destructively reset the worktree to {reference} (discards uncommitted work).",
                "critical",
            )
            if not decision.startswith("Approved"):
                return f"Hard revert not approved: {decision}"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        return await git_tools.git_revert(driver, session_id, reference, mode)


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
        path: Annotated[
            str | None,
            Field(
                description="Repository folder when it is not /workspace itself, e.g. "
                "'backend-services' for /workspace/backend-services (found automatically when "
                "there is only one)."
            ),
        ] = None,
    ) -> str:
        target = registry._sandbox_target("git_push")
        if not target:
            return "Error: Sandbox not initialized for git_push"
        _, session_id = target
        await registry._ensure_sandbox(session_id)
        return await git_tools.git_push(
            session_id,
            branch,
            force_with_lease,
            registry._git_engine,
            policy=registry._git_policy(),
            path=path,
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
        path: Annotated[
            str | None,
            Field(description="Repository folder when it is not /workspace itself (see git_push)."),
        ] = None,
    ) -> str:
        target = registry._sandbox_target("git_open_pr")
        if not target:
            return "Error: Sandbox not initialized for git_open_pr"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        result = await git_tools.git_open_pr(
            driver,
            session_id,
            title,
            body,
            target_branch,
            registry._git_engine,
            policy=registry._git_policy(),
            path=path,
        )
        follow = registry._turn_callback("github_watch")
        if follow and (pull := git_tools.parse_pull_request_url(result)):
            repository, number = pull
            await follow(repository, number, None, True)
            result += "\nThis session now follows the pull request: CI, reviews and comments will reach you."
        return result
