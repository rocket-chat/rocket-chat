"""Incoming forge webhooks: verified here, handed to the services that act on them.

A delivery may report on a pull request a session follows (``deliver_repo_event``) and may
start or wake a session through the organization's triggers (``services/triggers.py``).
"""

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from git_engine.engine import GitEngine

from api.services.github_events import deliver_repo_event
from api.services.ticket_trackers import TrackerSignatureError, jira_event, linear_event
from api.services.trigger_intake import receive_trigger_event, webhook_identity
from specifications.interfaces.automation import TriggerEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["GitHub Webhooks"])


def _git_engine(request: Request) -> GitEngine:
    return getattr(request.app.state, "git_engine", None) or GitEngine(
        driver=getattr(request.app.state, "sandbox_driver", None)
    )


async def _deliver_to_followers(
    request: Request, git_engine: GitEngine, raw_body: bytes, headers: dict[str, Any]
) -> list[str]:
    """Pass a repository event on to the sessions that follow that pull request or branch."""
    event = git_engine.parse_repo_event(raw_body, headers)
    if event is None:
        return []
    org_id, _ = await webhook_identity(request.app, event.actor, event.installation_id)
    return await deliver_repo_event(request.app, org_id, event)


@router.post("/github", status_code=status.HTTP_200_OK)
async def github_webhook_endpoint(
    request: Request,
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    x_github_event: str | None = Header(None, alias="X-GitHub-Event"),
    x_github_delivery: str | None = Header(None, alias="X-GitHub-Delivery"),
) -> dict[str, Any]:
    """Signed GitHub webhooks: reports for the sessions that follow a pull request, and events
    for the organization's triggers (issues, comments, pull requests, review comments, failed
    workflow runs). Each delivery starts at most one session, whatever the number of replicas."""
    raw_body = await request.body()
    headers = dict(request.headers)
    git_engine = _git_engine(request)
    try:
        event = git_engine.trigger_event(raw_body, headers)
    except ValueError as err:
        logger.warning("Rejected invalid GitHub webhook signature: %s", err)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Webhook authentication failed: {err}",
        ) from err

    delivered = await _deliver_to_followers(request, git_engine, raw_body, headers)
    outcome = await receive_trigger_event(request.app, event) if event is not None else None
    if outcome is None or outcome.status == "ignored":
        if delivered:
            return {"status": "delivered", "sessions": delivered}
        return {
            "status": "ignored",
            "reason": outcome.reason if outcome else "No actionable trigger in webhook event",
        }
    if outcome.status == "duplicate":
        logger.info("Dropping duplicate GitHub webhook delivery: %s", x_github_delivery)
        return {"status": "ignored", "reason": "Duplicate webhook delivery"}
    return {
        "status": outcome.status,
        "session_id": outcome.session_id,
        "trigger_id": outcome.trigger_id,
        "issue_or_pr_number": event.number if event else 0,
        "repo": event.repository if event else "",
    }


@router.post("/gitlab", status_code=status.HTTP_200_OK)
async def gitlab_webhook_endpoint(request: Request) -> dict[str, Any]:
    """GitLab webhooks (``X-Gitlab-Token`` = ``GITLAB_WEBHOOK_SECRET``): merge requests,
    pipelines and notes for the sessions that follow a merge request, and issues, merge
    requests, notes and failed pipelines for the organization's ``gitlab_*`` triggers. The
    organization is ``?org=`` (as the operator connected the project), else the default."""
    raw_body = await request.body()
    headers = dict(request.headers)
    host = _git_engine(request).gitlab
    try:
        event = host.trigger_event(raw_body, headers)
        repo_event = host.repo_event(raw_body, headers)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(err)) from err
    org_id = request.query_params.get("org") or (await webhook_identity(request.app, "", None))[0]
    delivered = await deliver_repo_event(request.app, org_id, repo_event) if repo_event else []
    outcome = await receive_trigger_event(request.app, event, org_id=org_id) if event else None
    if outcome is not None and outcome.status not in ("ignored", "duplicate"):
        return {"status": outcome.status, "session_id": outcome.session_id}
    if delivered:
        return {"status": "delivered", "sessions": delivered}
    return {"status": "ignored", "reason": outcome.reason if outcome else "No actionable event"}


async def _tracker_webhook(
    request: Request, parse: Callable[[bytes, dict[str, str]], TriggerEvent | None]
) -> dict[str, Any]:
    raw_body = await request.body()
    try:
        event = parse(raw_body, dict(request.headers))
    except TrackerSignatureError as err:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(err)) from err
    if event is None:
        return {"status": "ignored", "reason": "Not an issue event"}
    # The organization the operator connected the tracker for (``?org=``), else the default.
    org_id = request.query_params.get("org") or None
    outcome = await receive_trigger_event(request.app, event, org_id=org_id)
    return {"status": outcome.status, "session_id": outcome.session_id, "reason": outcome.reason}


@router.post("/jira", status_code=status.HTTP_200_OK)
async def jira_webhook_endpoint(request: Request) -> dict[str, Any]:
    """Signed Jira issue webhooks (``JIRA_WEBHOOK_SECRET``) for ``jira_issue`` triggers."""
    return await _tracker_webhook(request, jira_event)


@router.post("/linear", status_code=status.HTTP_200_OK)
async def linear_webhook_endpoint(request: Request) -> dict[str, Any]:
    """Signed Linear issue webhooks (``LINEAR_WEBHOOK_SECRET``) for ``linear_issue`` triggers."""
    return await _tracker_webhook(request, linear_event)
