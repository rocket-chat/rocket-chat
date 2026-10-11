"""Getting code: cloning repositories and read-only GitHub lookups."""

import logging
from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from agent_core.tools import github_tools
from agent_core.tools import repository as repository_tools

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register(registry: "ToolRegistry") -> None:
    @registry._tool(
        description=(
            "Clone a GitHub repository into the sandbox at /workspace/<name> and describe it "
            "(branch, last commit, top-level files). The sandbox starts BLANK: nothing is "
            "checked out until you clone it, and many tasks need no repository at all. When "
            "one is needed, find it first (user's request, `kb_search`, skills, "
            "`github_find_repositories`), then clone. Idempotent: an existing clone is "
            "refreshed. Authentication is automatic."
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
    ) -> str:
        target = registry._sandbox_target("clone_repository")
        if not target:
            return "Error: Sandbox not initialized for clone_repository"
        driver, session_id = target
        await registry._ensure_sandbox(session_id)
        outcome = await repository_tools.clone_into_workspace(
            driver, session_id, repository, branch, directory
        )
        cloned = registry._turn_callback("repository_cloned")
        if outcome.folder and cloned:
            if note := await cloned(session_id, outcome.folder):
                return f"{outcome.message}\n{note}"
        return outcome.message

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
        access = await registry._github_access()
        return await github_tools.find_repositories(access, query, owner)

    @registry._tool(
        description=(
            "Read one file, or list a directory, of a GitHub repository WITHOUT cloning or "
            "starting a sandbox (README, AGENTS.md, a config file, the repository root). "
            "Use it to understand a repository before deciding to clone it."
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
        access = await registry._github_access()
        return await github_tools.read_file(access, repository, path, ref)

    @registry._tool(
        description=(
            "Look at GitHub issues and pull requests: pass `number` for one with its "
            "description and latest comments (a PR also shows its branches and size), "
            "`query` to search, or neither to list the latest. Read-only."
        )
    )
    async def github_issues(
        repository: Annotated[str, Field(description="owner/name or a github.com URL.")],
        number: Annotated[int | None, Field(description="Issue or PR number.")] = None,
        query: Annotated[
            str | None, Field(description="Search text (GitHub issue search syntax).")
        ] = None,
        state: Annotated[str, Field(description="open, closed or all (when listing).")] = "open",
    ) -> str:
        access = await registry._github_access()
        return await github_tools.issues(access, repository, number, query, state)
