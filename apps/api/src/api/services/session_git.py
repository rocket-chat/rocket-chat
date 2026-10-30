"""A session's git credentials and publishing policy outside a turn (the cockpit's Git tab).

The same resolution a turn makes before its git tools run (``TurnRunner._resolve_git`` and
``_turn_policy``): the session owner's credentials, the requester's ``github.publish_as`` (``bot``
only with ``publish_as_bot``), the organization's signing completed with the member's key. So a
person publishing from the cockpit publishes exactly as the agent would for them.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, replace
from typing import Any

from agent_core.tools import github_client
from agent_core.tools.github_prs import REVIEW_THREADS_QUERY

from api.services.git_credentials import GitCredentialBundle, resolve_session_git_credentials
from api.services.git_signing import (
    SIGNED_MODE,
    effective_signing_policy,
    member_signing_for_turn,
)
from api.services.session_support import _repo_slug
from api.services.settings import effective_settings
from api.services.turn_service import TurnService
from specifications.interfaces.agent import GithubAccess, GitPolicy
from specifications.interfaces.sandbox import SandboxDriverProtocol
from specifications.interfaces.session import SessionRecord


@dataclass(frozen=True)
class SessionGit:
    bundle: GitCredentialBundle
    policy: GitPolicy


async def session_git(
    db_mgr: Any, git_engine: Any, record: SessionRecord, requester_id: str, agent: Any
) -> SessionGit:
    """The credentials and policy the session's git tools would use for ``requester_id``."""
    org, owner = record.tenant_org_id, record.tenant_user_id
    member_prefs = dict(await effective_settings(db_mgr, org, "user_preferences", owner))
    bundle = await resolve_session_git_credentials(
        user_id=owner,
        org_id=org,
        git_engine=git_engine,
        db_mgr=db_mgr,
        user_overrides=member_prefs,
        repo=_repo_slug(record.git_repo),
    )
    policy = await TurnService._git_policy(db_mgr, org, requester_id, agent)
    if policy.signing == SIGNED_MODE:
        signing = await member_signing_for_turn(db_mgr, org, owner, member_prefs)
        if signing is not None:
            bundle = replace(bundle, signing=signing, author_email=signing.commit_email)
    completed = replace(
        policy,
        user_name=policy.user_name or bundle.author_name,
        user_email=policy.user_email or bundle.author_email,
        user_token=bundle.user_token,
    )
    return SessionGit(bundle=bundle, policy=effective_signing_policy(completed, bundle.signing))


# --- what the Git tab shows -----------------------------------------------------------------

MAX_COMMITS_SHOWN = 30
MAX_THREADS_SHOWN = 50
_FIELD = "\x1f"


@dataclass(frozen=True)
class CommitView:
    sha: str
    author: str
    timestamp: int
    subject: str


@dataclass(frozen=True)
class BranchState:
    repository: str
    folder: str
    branch: str
    base: str
    ahead: int
    behind: int
    uncommitted: int
    commits: tuple[CommitView, ...]


@dataclass(frozen=True)
class CheckView:
    name: str
    status: str
    conclusion: str | None
    url: str


@dataclass(frozen=True)
class ThreadView:
    id: str
    resolved: bool
    path: str
    line: int | None
    comments: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PullView:
    number: int
    url: str
    title: str
    state: str
    draft: bool
    head_sha: str
    checks: tuple[CheckView, ...]
    threads: tuple[ThreadView, ...]


async def _git(driver: SandboxDriverProtocol, session_id: str, folder: str, command: str) -> str:
    result = await driver.exec_command(
        session_id, f"git -C {shlex.quote(folder)} {command}", timeout_seconds=20
    )
    return (result.stdout or "").strip() if result.exit_code == 0 else ""


async def branch_state(
    driver: SandboxDriverProtocol, session_id: str, folder: str, repository: str
) -> BranchState:
    """The checked-out branch against the remote default branch: ahead/behind, its commits and
    how many files are not committed yet."""
    branch = await _git(driver, session_id, folder, "rev-parse --abbrev-ref HEAD")
    remote_head = await _git(
        driver, session_id, folder, "symbolic-ref --short refs/remotes/origin/HEAD"
    )
    base = remote_head.removeprefix("origin/") or "main"
    counts = await _git(
        driver,
        session_id,
        folder,
        f"rev-list --left-right --count origin/{shlex.quote(base)}...HEAD",
    )
    behind, _, ahead = counts.partition("\t")
    log = await _git(
        driver,
        session_id,
        folder,
        f"log --format=%H{_FIELD}%an{_FIELD}%at{_FIELD}%s -n {MAX_COMMITS_SHOWN} "
        f"origin/{shlex.quote(base)}..HEAD",
    )
    commits = []
    for line in log.splitlines():
        parts = line.split(_FIELD)
        if len(parts) == 4 and parts[2].isdigit():
            commits.append(CommitView(parts[0], parts[1], int(parts[2]), parts[3]))
    status = await _git(driver, session_id, folder, "status --porcelain --untracked-files=all")
    return BranchState(
        repository=repository,
        folder=folder,
        branch=branch,
        base=base,
        ahead=int(ahead) if ahead.strip().isdigit() else 0,
        behind=int(behind) if behind.strip().isdigit() else 0,
        uncommitted=len([line for line in status.splitlines() if line.strip()]),
        commits=tuple(commits),
    )


def _thread(node: dict[str, Any]) -> ThreadView:
    comments = tuple(
        (str((c.get("author") or {}).get("login") or ""), str(c.get("body") or ""))
        for c in (node.get("comments") or {}).get("nodes") or []
    )
    line = node.get("line")
    return ThreadView(
        id=str(node.get("id") or ""),
        resolved=bool(node.get("isResolved")),
        path=str(node.get("path") or ""),
        line=int(line) if isinstance(line, int) else None,
        comments=comments,
    )


async def pull_request_state(access: GithubAccess, slug: str, branch: str) -> PullView | None:
    """The open (else latest) pull request of ``branch``, its checks on the head commit and
    its review threads."""
    owner = slug.split("/", 1)[0]
    pulls = await github_client.request(
        access, "GET", f"/repos/{slug}/pulls", params={"head": f"{owner}:{branch}", "state": "all"}
    )
    if not isinstance(pulls, list) or not pulls:
        return None
    pull = next((p for p in pulls if p.get("state") == "open"), pulls[0])
    sha = str(pull["head"]["sha"])
    runs = await github_client.request(
        access, "GET", f"/repos/{slug}/commits/{sha}/check-runs", params={"per_page": 100}
    )
    checks = tuple(
        CheckView(
            name=str(r.get("name") or ""),
            status=str(r.get("status") or ""),
            conclusion=r.get("conclusion"),
            url=str(r.get("html_url") or ""),
        )
        for r in (runs or {}).get("check_runs", [])
    )
    name = slug.split("/", 1)[1]
    data = await github_client.graphql(
        access, REVIEW_THREADS_QUERY, {"owner": owner, "name": name, "number": int(pull["number"])}
    )
    nodes = data["repository"]["pullRequest"]["reviewThreads"]["nodes"] if data else []
    return PullView(
        number=int(pull["number"]),
        url=str(pull.get("html_url") or ""),
        title=str(pull.get("title") or ""),
        state="merged" if pull.get("merged_at") else str(pull.get("state") or ""),
        draft=bool(pull.get("draft")),
        head_sha=sha,
        checks=checks,
        threads=tuple(_thread(n) for n in nodes[:MAX_THREADS_SHOWN]),
    )
