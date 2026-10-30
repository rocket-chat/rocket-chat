"""GitHub webhooks as ``TriggerEvent`` values, for the organization's triggers to match.

Only what the platform may act on is returned: comments and pull requests from repository
insiders are ``trusted``; a stranger's comment on a public repository is parsed (so it can be
logged) but never starts work. Labels are added by people with triage access, and a failed run
is trusted when it ran on the repository itself (not a fork).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from specifications.interfaces.automation import TriggerEvent

TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
AGENT_MENTIONS = ("@rocketchat", "@bot", "/agent", "/fix", "/review")
_PULL_REQUEST_ACTIONS = ("opened", "reopened", "synchronize", "labeled", "ready_for_review")


def parse_github_trigger(raw_body: bytes, headers: dict[str, Any]) -> TriggerEvent | None:
    """The trigger event of a verified GitHub webhook, or None when it is not one."""
    header_map = {k.lower(): v for k, v in headers.items()}
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    parser = _PARSERS.get(str(header_map.get("x-github-event") or ""))
    event = parser(payload) if parser else None
    if event is None:
        return None
    return replace(
        event,
        delivery_id=str(header_map.get("x-github-delivery") or ""),
        repository=str((payload.get("repository") or {}).get("full_name") or "").lower(),
        actor=str((payload.get("sender") or {}).get("login") or ""),
        installation_id=_installation(payload),
    )


def _issue_labeled(payload: dict[str, Any]) -> TriggerEvent | None:
    if payload.get("action") != "labeled":
        return None
    issue = payload.get("issue") or {}
    added = str((payload.get("label") or {}).get("name") or "")
    return TriggerEvent(
        source="github_issue_label",
        number=int(issue.get("number") or 0),
        title=str(issue.get("title") or ""),
        body=str(issue.get("body") or ""),
        # The label just added: an issue that already carried it must not start work again
        # when another label is added.
        labels=(added,) if added else (),
        url=str(issue.get("html_url") or ""),
        action="labeled",
        # Labelling needs triage access to the repository.
        trusted=True,
    )


def _issue_comment(payload: dict[str, Any]) -> TriggerEvent | None:
    if payload.get("action") != "created":
        return None
    issue, comment = payload.get("issue") or {}, payload.get("comment") or {}
    body = str(comment.get("body") or "")
    return TriggerEvent(
        source="github_issue_comment",
        number=int(issue.get("number") or 0),
        title=str(issue.get("title") or ""),
        body=body,
        labels=_labels(issue),
        url=str(comment.get("html_url") or ""),
        action="created",
        trusted=comment.get("author_association") in TRUSTED_ASSOCIATIONS,
        mentions_agent=_mentions_agent(body),
    )


def _pull_request(payload: dict[str, Any]) -> TriggerEvent | None:
    action = str(payload.get("action") or "")
    if action not in _PULL_REQUEST_ACTIONS:
        return None
    pr = payload.get("pull_request") or {}
    if pr.get("draft") and action != "ready_for_review":
        return None
    return TriggerEvent(
        source="github_pr",
        number=int(pr.get("number") or 0),
        title=str(pr.get("title") or ""),
        body=str(pr.get("body") or ""),
        labels=_labels(pr),
        branch=str((pr.get("head") or {}).get("ref") or ""),
        base_branch=str((pr.get("base") or {}).get("ref") or ""),
        head_sha=str((pr.get("head") or {}).get("sha") or ""),
        url=str(pr.get("html_url") or ""),
        action=action,
        trusted=pr.get("author_association") in TRUSTED_ASSOCIATIONS,
    )


def _review_comment(payload: dict[str, Any]) -> TriggerEvent | None:
    if payload.get("action") != "created":
        return None
    pr, comment = payload.get("pull_request") or {}, payload.get("comment") or {}
    body = str(comment.get("body") or "")
    location = f"`{comment.get('path')}` line {comment.get('line') or comment.get('original_line')}"
    return TriggerEvent(
        source="github_pr_review_comment",
        number=int(pr.get("number") or 0),
        title=str(pr.get("title") or ""),
        body=f"Review comment on {location}:\n{body}",
        labels=_labels(pr),
        branch=str((pr.get("head") or {}).get("ref") or ""),
        head_sha=str(comment.get("commit_id") or ""),
        url=str(comment.get("html_url") or ""),
        action="created",
        trusted=comment.get("author_association") in TRUSTED_ASSOCIATIONS,
        mentions_agent=_mentions_agent(body),
    )


def _workflow_run(payload: dict[str, Any]) -> TriggerEvent | None:
    run = payload.get("workflow_run") or {}
    if payload.get("action") != "completed" or run.get("conclusion") != "failure":
        return None
    pulls = run.get("pull_requests") or []
    repository = str((payload.get("repository") or {}).get("full_name") or "")
    head_repository = str((run.get("head_repository") or {}).get("full_name") or "")
    return TriggerEvent(
        source="github_workflow_failed",
        number=int(pulls[0].get("number") or 0) if pulls else 0,
        title=f"{run.get('name') or 'Workflow'} failed on {run.get('head_branch') or 'a branch'}",
        body=str(run.get("display_title") or ""),
        branch=str(run.get("head_branch") or ""),
        head_sha=str(run.get("head_sha") or ""),
        url=str(run.get("html_url") or ""),
        action="completed",
        # A run of a fork's code reports on code nobody in the repository wrote.
        trusted=bool(head_repository) and head_repository == repository,
    )


_PARSERS: dict[str, Callable[[dict[str, Any]], TriggerEvent | None]] = {
    "issues": _issue_labeled,
    "issue_comment": _issue_comment,
    "pull_request": _pull_request,
    "pull_request_review_comment": _review_comment,
    "workflow_run": _workflow_run,
}


def _labels(item: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(label.get("name") or "")
        for label in item.get("labels") or []
        if isinstance(label, dict)
    )


def _mentions_agent(body: str) -> bool:
    lowered = body.lower()
    return any(mention in lowered for mention in AGENT_MENTIONS)


def _installation(payload: dict[str, Any]) -> int | None:
    raw = (payload.get("installation") or {}).get("id")
    return int(raw) if isinstance(raw, int) else None
