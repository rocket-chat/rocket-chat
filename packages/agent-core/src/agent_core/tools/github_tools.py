"""Read-only GitHub tools: find repositories, read files, look at issues and pull requests.

They let the agent work out *whether* and *which* repository a task needs before it starts a
sandbox or clones anything. They call the GitHub REST API directly with the member's linked
account or the organization's App installation token (``GithubAccess``); the token never
appears in a result. Everything returned is bounded, and failures come back as plain advice
because the agent, not a person, reads them.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

import httpx

from agent_core.tools.clamp import clamp_output
from agent_core.tools.repository import InvalidRepositoryError, parse_repository
from specifications.interfaces.agent import GithubAccess

TIMEOUT_SECONDS = 15.0
MAX_FILE_BYTES = 200_000
MAX_LIST = 20
BODY_CHARS = 3000
COMMENT_CHARS = 600


def _headers(access: GithubAccess) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "rocket-chat-agent",
    }
    if access.token:
        headers["Authorization"] = f"Bearer {access.token}"
    return headers


class _GithubError(Exception):
    """A failure already phrased for the agent."""


async def _get(access: GithubAccess, path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{access.api_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=_headers(access), params=params)
    except httpx.HTTPError as err:
        raise _GithubError(f"Could not reach GitHub ({type(err).__name__}).") from err
    if response.status_code == 404:
        raise _GithubError(
            "Not found, or not visible to the credential this session has"
            + ("" if access.token else " (no GitHub credential is available: only public data)")
            + ". Check the name; if it is private the user must link GitHub or an administrator "
            "must map the GitHub App installation."
        )
    if response.status_code in (401, 403):
        remaining = response.headers.get("x-ratelimit-remaining")
        reason = (
            "the rate limit is exhausted"
            if remaining == "0"
            else "the credential is missing, expired or not allowed to see it"
        )
        raise _GithubError(
            f"GitHub refused the request: {reason}. Do not retry or hunt for tokens."
        )
    if response.status_code >= 400:
        raise _GithubError(f"GitHub answered HTTP {response.status_code}.")
    return response.json()


def _repo_line(repo: dict[str, Any]) -> str:
    bits = [str(repo.get("language") or "").strip(), f"★{repo.get('stargazers_count', 0)}"]
    if repo.get("private"):
        bits.append("private")
    if repo.get("archived"):
        bits.append("archived")
    description = (repo.get("description") or "").strip().replace("\n", " ")[:120]
    updated = str(repo.get("pushed_at") or repo.get("updated_at") or "")[:10]
    detail = ", ".join(b for b in bits if b)
    return (
        f"{repo.get('full_name')} - {description or 'no description'} ({detail}; updated {updated})"
    )


async def find_repositories(
    access: GithubAccess, query: str | None = None, owner: str | None = None
) -> str:
    """List repositories by owner and/or search text; with neither, those the credential can see."""
    try:
        if owner and not query:
            try:
                repos = await _get(
                    access, f"/orgs/{owner}/repos", {"sort": "pushed", "per_page": MAX_LIST}
                )
            except _GithubError:
                repos = await _get(
                    access, f"/users/{owner}/repos", {"sort": "pushed", "per_page": MAX_LIST}
                )
        elif query:
            scope = f" user:{owner}" if owner else ""
            found = await _get(
                access,
                "/search/repositories",
                {"q": f"{query}{scope} in:name,description", "per_page": MAX_LIST},
            )
            repos = found.get("items", [])
        else:
            repos = await _get(access, "/user/repos", {"sort": "pushed", "per_page": MAX_LIST})
    except _GithubError as err:
        return f"Error: {err}"
    if not repos:
        return "No repositories matched. Try a different spelling or the owner's name."
    lines = [_repo_line(r) for r in repos[:MAX_LIST]]
    return f"{len(lines)} repositories (most recently active first):\n" + "\n".join(lines)


async def read_file(
    access: GithubAccess, repository: str, path: str = "", ref: str | None = None
) -> str:
    """A file's text, or a directory listing, without cloning the repository."""
    try:
        repo = parse_repository(repository)
    except InvalidRepositoryError as err:
        return f"Error: {err}"
    clean = path.strip().strip("/")
    if ".." in clean.split("/"):
        return "Error: the path must not contain '..'."
    try:
        data = await _get(
            access, f"/repos/{repo.slug}/contents/{clean}", {"ref": ref} if ref else None
        )
    except _GithubError as err:
        return f"Error: {err}"
    if isinstance(data, list):
        entries = [
            f"{'dir ' if item.get('type') == 'dir' else 'file'}  {item.get('path')}"
            for item in data[:100]
        ]
        return f"{repo.slug}:{clean or '/'} ({len(data)} entries)\n" + "\n".join(entries)
    if data.get("type") != "file":
        return f"{repo.slug}:{clean} is a {data.get('type')}, not a file."
    if int(data.get("size") or 0) > MAX_FILE_BYTES:
        return (
            f"{repo.slug}:{clean} is {data.get('size')} bytes, too large to read here; "
            "clone the repository to work with it."
        )
    try:
        text = base64.b64decode(data.get("content") or "").decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return f"{repo.slug}:{clean} is not a text file."
    return f"{repo.slug}:{clean}\n" + clamp_output(text, 12000)


def _issue_line(issue: dict[str, Any]) -> str:
    labels = ", ".join(label.get("name", "") for label in issue.get("labels", []))
    kind = "PR" if issue.get("pull_request") else "issue"
    author = (issue.get("user") or {}).get("login", "?")
    return (
        f"#{issue.get('number')} [{issue.get('state')} {kind}] {issue.get('title')} "
        f"({labels or 'no labels'}; by {author})"
    )


async def issues(
    access: GithubAccess,
    repository: str,
    number: int | None = None,
    query: str | None = None,
    state: str = "open",
) -> str:
    """One issue or pull request with its latest comments, or a list / search of them."""
    try:
        repo = parse_repository(repository)
    except InvalidRepositoryError as err:
        return f"Error: {err}"
    try:
        if number is not None:
            issue = await _get(access, f"/repos/{repo.slug}/issues/{number}")
            comments = await _get(
                access, f"/repos/{repo.slug}/issues/{number}/comments", {"per_page": 30}
            )
            lines = [
                _issue_line(issue),
                clamp_output((issue.get("body") or "(no description)").strip(), BODY_CHARS),
            ]
            if issue.get("pull_request"):
                pull = await _get(access, f"/repos/{repo.slug}/pulls/{number}")
                lines.insert(
                    1,
                    f"branch {pull['head']['ref']} -> {pull['base']['ref']}; "
                    f"{pull.get('changed_files', '?')} files, +{pull.get('additions', '?')} "
                    f"-{pull.get('deletions', '?')}",
                )
            for comment in comments[-5:]:
                who = (comment.get("user") or {}).get("login", "?")
                lines.append(
                    f"--- {who}: {clamp_output((comment.get('body') or '').strip(), COMMENT_CHARS)}"
                )
            return "\n".join(lines)
        if query:
            found = await _get(
                access,
                "/search/issues",
                {"q": f"repo:{repo.slug} {query}", "per_page": MAX_LIST},
            )
            items = found.get("items", [])
        else:
            items = await _get(
                access,
                f"/repos/{repo.slug}/issues",
                {"state": state, "per_page": MAX_LIST, "sort": "updated"},
            )
    except _GithubError as err:
        return f"Error: {err}"
    if not items:
        return f"No {state} issues or pull requests matched in {repo.slug}."
    return f"{repo.slug} (latest first):\n" + "\n".join(_issue_line(i) for i in items[:MAX_LIST])
