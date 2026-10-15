"""GitHub beyond reading: pull requests, Actions, releases, search and any other API call.

These replace the ``gh`` CLI, which cannot work in a sandbox that holds no GitHub credential.
Every call runs on the platform; changes use the write credential the platform resolves for the
repository and say who acted. Which actions need a person's approval is decided per action
(``agent_core.permissions``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

from agent_core.tools import github_ci, github_generic, github_prs
from agent_core.tools.github_client import GithubError
from agent_core.tools.repository import InvalidRepositoryError, parse_repository
from specifications.interfaces.agent import GithubAccess

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

Repository = Annotated[str, Field(description="owner/name or a github.com URL.")]
PullAction = Literal[
    "list", "view", "files", "diff", "checks", "review_threads", "edit", "ready", "draft",
    "request_reviewers", "comment", "review", "reply_to_thread", "resolve_thread", "close",
    "reopen", "merge",
]  # fmt: skip


async def _write(registry: ToolRegistry, slug: str) -> GithubAccess:
    resolve = registry._turn_callback("github_write_access")
    if resolve is None:
        raise GithubError("Changes on GitHub are not available in this session.")
    access: GithubAccess = await resolve(slug)
    if not access.token:
        raise GithubError(
            "No GitHub credential can make changes in this repository: the member has not "
            "linked GitHub and no GitHub App installation for it is mapped to the organization."
        )
    return access


async def _guarded(work: Any) -> str:
    try:
        return str(await work)
    except (GithubError, InvalidRepositoryError) as err:
        return f"Error: {err}"


def register(registry: ToolRegistry) -> None:
    _register_pulls(registry)
    _register_ci(registry)
    _register_api(registry)


def _register_pulls(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Work with a pull request (what `gh pr` does). Read: list, view, files, diff (paged "
            "by file), checks, review_threads (with comment and thread ids). Change: edit "
            "(title/body/base), ready, draft, request_reviewers, comment, review "
            "(event comment|request_changes|approve), reply_to_thread (comment_id), "
            "resolve_thread (thread_id), close, reopen, merge (needs approval). Open a pull "
            "request with git_open_pr."
        )
    )
    async def github_pull_request(
        repository: Repository,
        action: Annotated[PullAction, Field(description="What to do.")],
        number: Annotated[int | None, Field(description="Pull request number.")] = None,
        title: Annotated[str | None, Field(description="New title (edit).")] = None,
        body: Annotated[
            str | None, Field(description="Text: comment, review, reply or new description.")
        ] = None,
        base: Annotated[str | None, Field(description="New base branch (edit).")] = None,
        reviewers: Annotated[
            list[str] | None, Field(description="GitHub logins (request_reviewers).")
        ] = None,
        event: Annotated[
            str | None, Field(description="comment, request_changes or approve (review).")
        ] = None,
        comment_id: Annotated[int | None, Field(description="Review comment to reply to.")] = None,
        thread_id: Annotated[str | None, Field(description="Review thread to resolve.")] = None,
        merge_method: Annotated[
            str | None, Field(description="merge, squash (default) or rebase.")
        ] = None,
        state: Annotated[str, Field(description="open, closed or all (list).")] = "open",
        page: Annotated[int, Field(description="Page of files for diff (10 files a page).")] = 1,
    ) -> str:
        fields = {
            "title": title, "body": body, "base": base, "reviewers": reviewers, "event": event,
            "comment_id": comment_id, "thread_id": thread_id, "merge_method": merge_method,
        }  # fmt: skip
        return await _guarded(_pull(registry, repository, action, number, fields, state, page))


async def _pull(
    registry: ToolRegistry,
    repository: str,
    action: str,
    number: int | None,
    fields: dict[str, Any],
    state: str,
    page: int,
) -> str:
    slug = parse_repository(repository).slug
    if action == "list":
        return await github_prs.list_pulls(await registry._github_access(), slug, state)
    if number is None:
        raise GithubError("Give the pull request number.")
    if action in github_prs.PR_WRITE_ACTIONS:
        return await github_prs.write_pull(
            await _write(registry, slug), slug, number, action, fields
        )
    read = await registry._github_access()
    if action == "view":
        from agent_core.tools import github_tools

        return await github_tools.issues(read, slug, number)
    readers = {
        "files": lambda: github_prs.files(read, slug, number),
        "diff": lambda: github_prs.diff(read, slug, number, page),
        "checks": lambda: github_prs.checks(read, slug, number),
        "review_threads": lambda: github_prs.review_threads(read, slug, number),
    }
    return await readers[action]()


def _register_ci(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "GitHub Actions (what `gh run` does). Read: runs (optionally on a branch), run_view "
            "(its jobs and failed steps), job_logs (the end of a job's log, where failures "
            "are). Change: rerun_failed, cancel, dispatch_workflow (workflow + ref + inputs)."
        )
    )
    async def github_actions(
        repository: Repository,
        action: Annotated[
            Literal["runs", "run_view", "job_logs", "rerun_failed", "cancel", "dispatch_workflow"],
            Field(description="What to do."),
        ],
        branch: Annotated[str | None, Field(description="Branch to list runs of.")] = None,
        run_id: Annotated[int | None, Field(description="Workflow run id.")] = None,
        job_id: Annotated[int | None, Field(description="Job id (job_logs).")] = None,
        workflow: Annotated[
            str | None, Field(description="Workflow file or id (dispatch).")
        ] = None,
        ref: Annotated[str | None, Field(description="Branch or tag to run on (dispatch).")] = None,
        inputs: Annotated[
            dict[str, str] | None, Field(description="Workflow inputs (dispatch).")
        ] = None,
    ) -> str:
        fields = {"run_id": run_id, "workflow": workflow, "ref": ref, "inputs": inputs}
        return await _guarded(_ci(registry, repository, action, branch, run_id, job_id, fields))

    @registry._tool(
        description=(
            "GitHub releases (what `gh release` does): list, view (by tag), create or edit "
            "(tag_name, name, body, draft, prerelease, target_commitish)."
        )
    )
    async def github_releases(
        repository: Repository,
        action: Annotated[
            Literal["list", "view", "create", "edit"], Field(description="What to do.")
        ],
        tag_name: Annotated[str | None, Field(description="Release tag.")] = None,
        name: Annotated[str | None, Field(description="Release title.")] = None,
        body: Annotated[str | None, Field(description="Release notes.")] = None,
        draft: Annotated[bool | None, Field(description="Create as a draft.")] = None,
        prerelease: Annotated[bool | None, Field(description="Mark as a pre-release.")] = None,
        target_commitish: Annotated[
            str | None, Field(description="Branch or commit to tag.")
        ] = None,
    ) -> str:
        fields = {
            "tag_name": tag_name, "name": name, "body": body, "draft": draft,
            "prerelease": prerelease, "target_commitish": target_commitish,
        }  # fmt: skip
        return await _guarded(_release(registry, repository, action, fields))

    @registry._tool(
        description=(
            "Search GitHub (what `gh search` does): code, issues (and pull requests) or commits, "
            "optionally within one repository. Uses GitHub search syntax."
        )
    )
    async def github_search(
        kind: Annotated[Literal["code", "issues", "commits"], Field(description="What to search.")],
        query: Annotated[str, Field(description="Search text, GitHub syntax.")],
        repository: Annotated[str | None, Field(description="owner/name to search within.")] = None,
    ) -> str:
        return await _guarded(
            github_ci.search(await registry._github_access(), kind, query, repository)
        )


async def _ci(
    registry: ToolRegistry,
    repository: str,
    action: str,
    branch: str | None,
    run_id: int | None,
    job_id: int | None,
    fields: dict[str, Any],
) -> str:
    slug = parse_repository(repository).slug
    if action in github_ci.ACTIONS_WRITE:
        return await github_ci.write_run(await _write(registry, slug), slug, action, fields)
    read = await registry._github_access()
    if action == "runs":
        return await github_ci.runs(read, slug, branch)
    if action == "run_view" and run_id:
        return await github_ci.run_view(read, slug, run_id)
    if action == "job_logs" and job_id:
        return await github_ci.job_logs(read, slug, job_id)
    raise GithubError("Give run_id for run_view, job_id for job_logs.")


async def _release(
    registry: ToolRegistry, repository: str, action: str, fields: dict[str, Any]
) -> str:
    slug = parse_repository(repository).slug
    if action in github_ci.RELEASES_WRITE:
        return await github_ci.write_release(await _write(registry, slug), slug, action, fields)
    tag = fields.get("tag_name") if action == "view" else None
    return await github_ci.releases(await registry._github_access(), slug, tag)


def _register_api(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Any other GitHub REST or GraphQL call (what `gh api` does), when no dedicated "
            "github_* tool covers it. REST: method + path (e.g. GET /repos/o/r/branches), "
            "query, body, paginate. GraphQL: graphql (+ variables; a mutation needs "
            "repository). Reads need no approval; changes follow the permission mode, DELETE "
            "always asks. Secrets, keys, webhooks, protection, access and administration are "
            "refused."
        )
    )
    async def github_api(
        method: Annotated[str, Field(description="GET, POST, PATCH, PUT or DELETE.")] = "GET",
        path: Annotated[str | None, Field(description="REST path, e.g. /repos/o/r/labels.")] = None,
        query: Annotated[dict[str, Any] | None, Field(description="Query parameters.")] = None,
        body: Annotated[dict[str, Any] | None, Field(description="JSON body.")] = None,
        paginate: Annotated[bool, Field(description="GET every page (up to 5).")] = False,
        graphql: Annotated[str | None, Field(description="A GraphQL query or mutation.")] = None,
        variables: Annotated[dict[str, Any] | None, Field(description="GraphQL variables.")] = None,
        repository: Annotated[
            str | None, Field(description="owner/name a mutation changes.")
        ] = None,
    ) -> str:
        arguments = {
            "method": method, "path": path, "query": query, "body": body, "paginate": paginate,
            "graphql": graphql, "variables": variables, "repository": repository,
        }  # fmt: skip
        write_for = registry._turn_callback("github_write_access")

        async def checked_write(slug: str) -> GithubAccess:
            return await _write(registry, slug)

        return await _guarded(
            github_generic.call(
                await registry._github_access(), checked_write if write_for else None, arguments
            )
        )
