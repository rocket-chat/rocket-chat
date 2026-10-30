"""The cockpit's Git tab: the branch, its pull request with checks and review threads, and
commit / push / open-PR buttons that publish exactly as the agent would for the person."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Literal

from agent_core.tools import git_tools
from agent_core.tools.github_client import GithubError
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.session_lookup import SessionCaller, session_for_request
from api.services.agent_access import AgentAccessDenied
from api.services.audit import AuditService
from api.services.github_read_access import clean_repository, github_read_access
from api.services.personas import get_persona
from api.services.session_git import (
    SessionGit,
    branch_state,
    pull_request_state,
    session_git,
)
from api.services.session_support import active_repository_of
from specifications.interfaces.events import EventType, SessionEvent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])

GitAction = Literal["commit", "push", "pull_request"]
# What a publish answers, as the conversation shows it (bounded).
SUMMARY_CHARS = 2_000


class CommitItem(BaseModel):
    sha: str
    author: str
    timestamp: int
    subject: str


class CheckItem(BaseModel):
    name: str
    status: str
    conclusion: str | None
    url: str


class ThreadItem(BaseModel):
    id: str
    resolved: bool
    path: str
    line: int | None
    comments: list[tuple[str, str]] = Field(..., description="(author, body) pairs")


class PullRequestItem(BaseModel):
    number: int
    url: str
    title: str
    state: str = Field(..., description="open, closed or merged")
    draft: bool
    head_sha: str
    checks: list[CheckItem]
    threads: list[ThreadItem]


class SessionGitView(BaseModel):
    repository: str = Field(..., description="owner/name of the active repository")
    folder: str
    branch: str
    base: str
    ahead: int
    behind: int
    uncommitted: int = Field(..., description="Files changed and not committed")
    commits: list[CommitItem]
    pull_request: PullRequestItem | None
    pull_request_error: str = Field("", description="Why the pull request could not be read")
    publish_as: str = Field(..., description="member or bot (github.publish_as)")
    signing: str = Field(..., description="github, member_gpg or none (github.signing)")


class CommitRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5_000)


class PullRequestRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    body: str = Field(default="", max_length=60_000)
    base: str | None = Field(default=None, max_length=255)


class GitActionResult(BaseModel):
    action: GitAction
    ok: bool
    summary: str
    url: str = ""


async def _resolved(request: Request, caller: SessionCaller) -> tuple[SessionGit, str, str]:
    record = caller.record
    folder = active_repository_of(record)
    repository = clean_repository(record.git_repo) or folder.rsplit("/", 1)[-1]
    if not folder:
        raise HTTPException(status_code=409, detail="The session has no active repository yet.")
    state = request.app.state
    db_mgr = getattr(state, "db_manager", None)
    try:
        agent = await get_persona(
            db_mgr, record.tenant_org_id, caller.user_id, record.metadata.get("agent_id")
        )
    except AgentAccessDenied:
        # Publishing follows the organization's policy whatever agent the session uses.
        agent = None
    resolved = await session_git(
        db_mgr, getattr(state, "git_engine", None), record, caller.user_id, agent
    )
    return resolved, folder, repository


@router.get("/sessions/{session_id}/git", response_model=SessionGitView)
async def get_session_git(session_id: str, request: Request) -> SessionGitView:
    """The active repository's branch against its base, its commits, and its pull request."""
    caller = await session_for_request(request, session_id, require_write=False)
    driver = getattr(request.app.state, "sandbox_driver", None)
    if driver is None:
        raise HTTPException(status_code=409, detail="This deployment has no sandboxes.")
    resolved, folder, repository = await _resolved(request, caller)
    state = await branch_state(driver, session_id, folder, repository)
    pull, error = None, ""
    if "/" in repository and state.branch and state.branch != "HEAD":
        access = await github_read_access(
            getattr(request.app.state, "git_engine", None),
            getattr(request.app.state, "db_manager", None),
            caller.record.tenant_org_id,
            resolved.bundle,
            repository,
        )
        try:
            pull = await pull_request_state(access, repository, state.branch)
        except (GithubError, KeyError, TypeError) as err:
            error = str(err)[:300]
    return SessionGitView(
        **{k: v for k, v in asdict(state).items() if k != "commits"},
        commits=[CommitItem(**asdict(c)) for c in state.commits],
        pull_request=PullRequestItem(**asdict(pull)) if pull else None,
        pull_request_error=error,
        publish_as=resolved.policy.publish_as,
        signing=resolved.policy.signing,
    )


async def _act(
    request: Request, session_id: str, action: GitAction, payload: dict[str, Any]
) -> GitActionResult:
    caller = await session_for_request(request, session_id, require_write=True)
    if caller.record.is_processing:
        # The agent may be editing or committing in the same tree right now.
        raise HTTPException(status_code=409, detail="Wait for the running turn to finish.")
    state = request.app.state
    driver, engine = getattr(state, "sandbox_driver", None), getattr(state, "git_engine", None)
    if driver is None:
        raise HTTPException(status_code=409, detail="This deployment has no sandboxes.")
    resolved, folder, repository = await _resolved(request, caller)
    summary = await _run(action, payload, driver, engine, session_id, folder, resolved)
    ok = not summary.startswith("Error")
    opened = "Pull request opened: "
    url = summary.removeprefix(opened).strip() if summary.startswith(opened) else ""
    await _record(request, caller, action, repository, summary, url, ok)
    return GitActionResult(action=action, ok=ok, summary=summary[:SUMMARY_CHARS], url=url)


async def _run(
    action: GitAction,
    payload: dict[str, Any],
    driver: Any,
    engine: Any,
    session_id: str,
    folder: str,
    resolved: SessionGit,
) -> str:
    policy = resolved.policy
    if action == "commit":
        return await git_tools.git_commit(
            driver,
            session_id,
            payload["message"],
            None,
            None,
            engine,
            policy=policy,
            workdir=folder,
        )
    if action == "push":
        return await git_tools.git_push(session_id, None, False, engine, policy=policy, path=folder)
    return await git_tools.git_open_pr(
        driver,
        session_id,
        payload["title"],
        payload["body"],
        payload.get("base"),
        engine,
        policy=policy,
        path=folder,
    )


async def _record(
    request: Request,
    caller: SessionCaller,
    action: GitAction,
    repository: str,
    summary: str,
    url: str,
    ok: bool,
) -> None:
    record = caller.record
    if ok and action != "commit":
        await AuditService(getattr(request.app.state, "db_manager", None)).record_git_publish(
            org_id=record.tenant_org_id,
            actor_user_id=caller.user_id,
            session_id=record.session_id,
            tool_name=f"cockpit:{action}",
            outcome=summary.splitlines()[0][:300] if summary else "",
        )
    payload: dict[str, Any] = {
        "action": action,
        "repository": repository,
        "summary": summary[:SUMMARY_CHARS],
        "decided_by": caller.user_id,
    }
    if url:
        payload["url"] = url
    await request.app.state.event_bus.publish(
        SessionEvent(
            session_id=record.session_id, event_type=EventType.GIT_PUBLISHED, payload=payload
        )
    )


@router.post("/sessions/{session_id}/git/commit", response_model=GitActionResult)
async def commit_session_changes(
    session_id: str, body: CommitRequest, request: Request
) -> GitActionResult:
    """Commit every change in the active repository, under the organization's authorship and
    signing policy (as the agent's ``git_commit``)."""
    return await _act(request, session_id, "commit", {"message": body.message})


@router.post("/sessions/{session_id}/git/push", response_model=GitActionResult)
async def push_session_branch(session_id: str, request: Request) -> GitActionResult:
    """Publish the branch through the platform's publish path (as the agent's ``git_push``)."""
    return await _act(request, session_id, "push", {})


@router.post("/sessions/{session_id}/git/pull-request", response_model=GitActionResult)
async def open_session_pull_request(
    session_id: str, body: PullRequestRequest, request: Request
) -> GitActionResult:
    """Publish the branch and open a pull request (as the agent's ``git_open_pr``)."""
    return await _act(request, session_id, "pull_request", body.model_dump())
