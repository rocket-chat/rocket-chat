"""Getting code: cloning repositories and read-only GitHub lookups."""

import logging
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field
from sandbox_driver.workspace import RepositoryNotFoundError

from agent_core.tools import github_prs, github_tools
from agent_core.tools import repository as repository_tools
from agent_core.tools.builtin import github as github_builtin
from agent_core.tools.github_client import GithubError

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register(registry: "ToolRegistry") -> None:
    _register_clone(registry)
    _register_watch(registry)
    _register_github_reads(registry)


def _register_clone(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Clone a GitHub repository into the sandbox at /workspace/<name> and describe it "
            "(branch, last commit, top-level files). The sandbox starts BLANK: nothing is "
            "checked out until you clone it, and many tasks need no repository at all. When "
            "one is needed, find it first (user's request, `kb_search`, skills, "
            "`github_find_repositories`), then clone. Idempotent: an existing clone is "
            "refreshed. Authentication is automatic. A fresh clone's result includes the "
            "repository's own AGENTS.md / CLAUDE.md: follow it."
        )
    )
    async def clone_repository(
        repository: Annotated[
            str,
            Field(description="owner/name or an https://github.com/owner/name URL."),
        ],
        branch: Annotated[
            str | None, Field(description="Branch to check out (default: the default branch).")
        ] = None,
        directory: Annotated[
            str | None,
            Field(description="Folder name under /workspace (default: the repository name)."),
        ] = None,
        depth: Annotated[
            int | None,
            Field(
                description="Only the latest N commits, for very large repositories (default: "
                "full history, file contents fetched on demand)."
            ),
        ] = None,
    ) -> str:
        target = registry._sandbox_target("clone_repository")
        if not target:
            return "Error: Sandbox not initialized for clone_repository"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        outcome = await repository_tools.clone_into_workspace(
            driver, session_id, repository, branch, directory, depth
        )
        message = outcome.message
        if outcome.folder and registry.remember_active_repository(outcome.folder):
            message += (
                f"\n{outcome.folder} is now the active repository: git, test and build tools "
                "use it when you name no folder."
            )
        if outcome.instructions:
            # In effect from the next step and rebuilt every turn, not left in this result where
            # the output clamp would cut it and pruning would take it away. A subagent has no
            # context note of its own, so it reads them here.
            if registry.add_standing_note(outcome.instructions):
                message += (
                    "\nThe repository's own instructions (AGENTS.md) are now in effect: they are "
                    "in your instructions from your next step. Follow them."
                )
            else:
                message += f"\n{outcome.instructions}"
        cloned = registry._turn_callback("repository_cloned")
        if outcome.folder and cloned:
            if note := await cloned(session_id, outcome.folder):
                return f"{message}\n{note}"
        return message

    @registry._tool(
        description=(
            "Make another cloned repository the active one: git, test and build tools and "
            "relative file paths use it when you name no folder, in this turn and later ones. "
            "Not needed to run a command elsewhere: `bash_exec` takes its folder as `cwd`, and "
            "the other tools take `path`."
        )
    )
    async def set_active_repository(
        path: Annotated[str, Field(description="The repository's folder, e.g. /workspace/api.")],
    ) -> str:
        target = registry._sandbox_target("set_active_repository")
        if not target:
            return "Error: Sandbox not initialized for set_active_repository"
        _, session_id = target
        await registry._ensure_sandbox(session_id)
        try:
            folder = await registry.repository_dir(session_id, path)
        except RepositoryNotFoundError as err:
            return f"Error: {err}"
        registry.choose_active_repository(folder)
        if chosen := registry._turn_callback("active_repository_chosen"):
            await chosen(session_id, folder)
        return f"{folder} is now the active repository."


def _register_watch(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Follow a pull request (or a branch) so this session hears about it: CI results, "
            "reviews, comments, merge or close. Use it to wait for CI or a review without "
            "polling or building a tool. Events reach you in a later turn on their own; a "
            "pull request you open with git_open_pr is followed automatically. Following "
            "stops by itself when the pull request is merged or closed."
        )
    )
    async def github_watch(
        repository: Annotated[str, Field(description="owner/name of the repository.")],
        pull_request: Annotated[
            int | None, Field(description="Pull request number to follow.")
        ] = None,
        branch: Annotated[
            str | None, Field(description="Follow CI on this branch instead (no pull request).")
        ] = None,
        wake: Annotated[
            bool,
            Field(
                description="Start a turn when something happens (default); false only records "
                "events for your next turn."
            ),
        ] = True,
    ) -> str:
        follow = registry._turn_callback("github_watch")
        if follow is None:
            return "Error: following GitHub activity is not available in this session."
        if pull_request is None and not branch:
            return "Error: give a pull_request number or a branch to follow."
        return str(await follow(repository, pull_request, branch, wake))

    @registry._tool(
        description="Stop following a pull request or branch (all of a repository's if neither is given)."
    )
    async def github_unwatch(
        repository: Annotated[str, Field(description="owner/name of the repository.")],
        pull_request: Annotated[int | None, Field(description="Pull request number.")] = None,
        branch: Annotated[str | None, Field(description="Branch name.")] = None,
    ) -> str:
        unfollow = registry._turn_callback("github_unwatch")
        if unfollow is None:
            return "Error: following GitHub activity is not available in this session."
        return str(await unfollow(repository, pull_request, branch))


def _register_github_reads(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Find GitHub repositories without a sandbox: by owner/organization (its most "
            "recently active repositories), by search text, or - with neither - those the "
            "user's credential can see. Use it to work out which repository a task refers "
            "to before cloning."
        )
    )
    async def github_find_repositories(
        query: Annotated[
            str | None, Field(description="Words from the name or description.")
        ] = None,
        owner: Annotated[
            str | None, Field(description="A user or organization to restrict to.")
        ] = None,
    ) -> str:
        access = await registry._github_access(owner)
        return await github_tools.find_repositories(access, query, owner)

    @registry._tool(
        description=(
            "Read one file, or list a directory, of a GitHub repository WITHOUT cloning or "
            "starting a sandbox (README, AGENTS.md, a config file, the repository root). "
            "Use it to understand a repository before deciding to clone it. Once the "
            "repository is cloned in the sandbox, do NOT use this: read and search the local "
            "files with `file_read`, `list_dir` and `grep_search` (faster, and they include "
            "your own changes)."
        )
    )
    async def github_read_file(
        repository: Annotated[str, Field(description="owner/name or a github.com URL.")],
        path: Annotated[
            str, Field(description="File or directory path; empty lists the root.")
        ] = "",
        ref: Annotated[
            str | None, Field(description="Branch, tag or commit (default branch).")
        ] = None,
    ) -> str:
        # Enforce what the description asks: a repository already in the running sandbox is read
        # there. Another ref is the one case where the GitHub copy differs from the clone.
        target = registry._sandbox_target("github_read_file") if ref is None else None
        if target and (folder := await repository_tools.local_clone_of(*target, repository)):
            return (
                f"{repository} is already cloned at {folder}. Read it there instead, with "
                f"`file_read`, `list_dir` and `grep_search` (faster, and it includes your own "
                f"changes). Use this tool only for a repository that is not cloned, or pass "
                f"`ref` to read another branch or commit."
            )
        access = await registry._github_access(repository)
        return await github_tools.read_file(access, repository, path, ref)

    @registry._tool(
        description=(
            "GitHub issues (what `gh issue` does). Read: pass `number` for one issue or pull "
            "request with its description and latest comments, `query` to search, or neither to "
            "list the latest. Change (action): create (title, body, labels, assignees), comment, "
            "edit, close, reopen."
        )
    )
    async def github_issues(
        repository: Annotated[str, Field(description="owner/name or a github.com URL.")],
        number: Annotated[int | None, Field(description="Issue or PR number.")] = None,
        query: Annotated[
            str | None, Field(description="Search text (GitHub issue search syntax).")
        ] = None,
        state: Annotated[str, Field(description="open, closed or all (when listing).")] = "open",
        action: Annotated[
            Literal["create", "comment", "edit", "close", "reopen"] | None,
            Field(description="A change to make; leave empty to read."),
        ] = None,
        title: Annotated[str | None, Field(description="Title (create, edit).")] = None,
        body: Annotated[str | None, Field(description="Text (create, comment, edit).")] = None,
        labels: Annotated[list[str] | None, Field(description="Labels (create, edit).")] = None,
        assignees: Annotated[list[str] | None, Field(description="Logins (create, edit).")] = None,
    ) -> str:
        if action is None:
            access = await registry._github_access(repository)
            return await github_tools.issues(access, repository, number, query, state)
        fields = {"title": title, "body": body, "labels": labels, "assignees": assignees}
        try:
            slug = repository_tools.parse_repository(repository).slug
            write = await github_builtin._write(registry, slug)
            return await github_prs.write_issue(write, slug, number, action, fields)
        except (GithubError, repository_tools.InvalidRepositoryError) as err:
            return f"Error: {err}"
