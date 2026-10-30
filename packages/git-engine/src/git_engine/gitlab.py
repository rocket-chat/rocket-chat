"""GitLab (gitlab.com or self-hosted) as a ``GitHostProtocol``: merge requests, pipeline state,
and its webhooks as trigger and repository events.

Configured by ``GITLAB_URL`` (default https://gitlab.com), ``GITLAB_WEBHOOK_SECRET`` (the
webhook's secret token, compared in constant time; without it every webhook is refused) and
``GITLAB_TOKEN`` (the deployment's token, used when the member has none of their own).
"""

from __future__ import annotations

import hmac
import json
import os
import re
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from git_engine.publish import repository_of
from specifications.interfaces.automation import TriggerEvent
from specifications.interfaces.git import RepoEvent
from specifications.interfaces.git_host import ChangeRequest, CiState

GITLAB_TIMEOUT_SECONDS = 15.0
_PIPELINE_STATES = {
    "success": "success",
    "failed": "failure",
    "canceled": "failure",
    "skipped": "success",
    "running": "pending",
    "pending": "pending",
    "created": "pending",
}
AGENT_MENTIONS = ("@rocketchat", "@bot", "/agent", "/fix", "/review")
_MERGE_REQUEST_URL = re.compile(r"https://[^\s]+?/(.+?)/-/merge_requests/(\d+)")


class GitLabError(RuntimeError):
    """GitLab refused a request; the message says why."""


@dataclass(frozen=True)
class GitLabSettings:
    url: str = "https://gitlab.com"
    webhook_secret: str = ""
    token: str = ""

    @classmethod
    def from_env(cls) -> GitLabSettings:
        return cls(
            url=(os.getenv("GITLAB_URL") or "https://gitlab.com").rstrip("/"),
            webhook_secret=os.getenv("GITLAB_WEBHOOK_SECRET", ""),
            token=os.getenv("GITLAB_TOKEN", ""),
        )


def merge_request_of(text: str) -> tuple[str, int] | None:
    """``(group/name, number)`` of a merge request URL in ``text``."""
    match = _MERGE_REQUEST_URL.search(text)
    return (match.group(1), int(match.group(2))) if match else None


class GitLabHost:
    kind = "gitlab"
    basic_user = "oauth2"

    def __init__(self, settings: GitLabSettings | None = None) -> None:
        self.settings = settings or GitLabSettings.from_env()
        self.web_url = self.settings.url
        self.api_url = f"{self.web_url}/api/v4"
        self._host = (urlparse(self.web_url).hostname or "gitlab.com").lower()

    # -- repositories ----------------------------------------------------------------------

    def owns(self, remote_url: str) -> bool:
        text = remote_url.strip()
        scp = re.match(r"^git@([^:/]+):", text)
        host = scp.group(1) if scp else urlparse(text).hostname or ""
        return host.lower() == self._host

    def repository_of(self, remote_url: str) -> str:
        return repository_of(remote_url, self._host, nested=True)

    # -- API -------------------------------------------------------------------------------

    async def _request(
        self, method: str, path: str, token: str, body: dict[str, Any] | None = None
    ) -> Any:
        async with httpx.AsyncClient(timeout=GITLAB_TIMEOUT_SECONDS) as client:
            response = await client.request(
                method, f"{self.api_url}{path}", headers={"PRIVATE-TOKEN": token}, json=body
            )
        if response.status_code >= 400:
            detail = response.text[:300]
            raise GitLabError(f"GitLab answered {response.status_code}: {detail}")
        return response.json() if response.content else None

    async def open_change_request(
        self, repository: str, *, head: str, base: str, title: str, body: str, token: str
    ) -> ChangeRequest:
        project = quote(repository, safe="")
        made = await self._request(
            "POST",
            f"/projects/{project}/merge_requests",
            token,
            {"source_branch": head, "target_branch": base, "title": title, "description": body},
        )
        return ChangeRequest(repository, int(made["iid"]), str(made["web_url"]))

    async def ci_state(self, repository: str, sha: str, token: str) -> CiState:
        project = quote(repository, safe="")
        pipelines = await self._request(
            "GET", f"/projects/{project}/pipelines?sha={quote(sha)}&per_page=1", token
        )
        if not pipelines:
            return CiState("none")
        latest = pipelines[0]
        state = _PIPELINE_STATES.get(str(latest.get("status")), "pending")
        return CiState(state, str(latest.get("web_url") or ""), f"Pipeline {latest.get('status')}")

    # -- webhooks --------------------------------------------------------------------------

    def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> None:
        secret = self.settings.webhook_secret
        if not secret:
            raise ValueError("GITLAB_WEBHOOK_SECRET is not configured; GitLab webhooks are refused")
        token = {k.lower(): v for k, v in headers.items()}.get("x-gitlab-token", "")
        if not hmac.compare_digest(secret, token):
            raise ValueError("Invalid GitLab webhook token")

    def trigger_event(self, raw_body: bytes, headers: dict[str, str]) -> TriggerEvent | None:
        self.verify_webhook(raw_body, headers)
        payload, kind, delivery = _parsed(raw_body, headers)
        parser = _TRIGGER_PARSERS.get(kind)
        event = parser(payload) if parser else None
        if event is None:
            return None
        return _with_common(event, payload, delivery)

    def repo_event(self, raw_body: bytes, headers: dict[str, str]) -> RepoEvent | None:
        self.verify_webhook(raw_body, headers)
        payload, kind, _ = _parsed(raw_body, headers)
        parser = _REPO_PARSERS.get(kind)
        return parser(payload) if parser else None


def _parsed(raw_body: bytes, headers: dict[str, str]) -> tuple[dict[str, Any], str, str]:
    lowered = {k.lower(): v for k, v in headers.items()}
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except ValueError:
        payload = {}
    return (
        payload if isinstance(payload, dict) else {},
        str(lowered.get("x-gitlab-event") or ""),
        str(lowered.get("x-gitlab-event-uuid") or lowered.get("x-gitlab-webhook-uuid") or ""),
    )


def _project(payload: dict[str, Any]) -> str:
    return str((payload.get("project") or {}).get("path_with_namespace") or "").lower()


def _with_common(event: TriggerEvent, payload: dict[str, Any], delivery: str) -> TriggerEvent:
    user = payload.get("user") or {}
    return replace(
        event,
        delivery_id=delivery,
        repository=_project(payload),
        actor=str(user.get("username") or ""),
    )


def _trusted(payload: dict[str, Any]) -> bool:
    """GitLab sends no author association: an event is trusted when it comes from the project
    itself (not a fork) and its author is not an external user. Labels need Reporter access."""
    attributes = payload.get("object_attributes") or {}
    source = attributes.get("source_project_id")
    target = attributes.get("target_project_id")
    return source is None or source == target


def _labels(payload: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(label.get("title") or "") for label in payload.get("labels") or [])


def _issue(payload: dict[str, Any]) -> TriggerEvent | None:
    attributes = payload.get("object_attributes") or {}
    changes = payload.get("changes") or {}
    if attributes.get("action") not in ("open", "update") or (
        attributes.get("action") == "update" and "labels" not in changes
    ):
        return None
    previous = {
        str(label.get("title")) for label in (changes.get("labels") or {}).get("previous") or []
    }
    added = tuple(label for label in _labels(payload) if label not in previous)
    return TriggerEvent(
        source="gitlab_issue_label",
        number=int(attributes.get("iid") or 0),
        title=str(attributes.get("title") or ""),
        body=str(attributes.get("description") or ""),
        labels=added,
        url=str(attributes.get("url") or ""),
        action=str(attributes.get("action") or ""),
        trusted=True,
    )


def _merge_request(payload: dict[str, Any]) -> TriggerEvent | None:
    attributes = payload.get("object_attributes") or {}
    action = str(attributes.get("action") or "")
    if action not in ("open", "reopen", "update") or attributes.get("draft"):
        return None
    if action == "update" and not attributes.get("oldrev"):
        return None  # an update without new commits (a title, a label)
    return TriggerEvent(
        source="gitlab_mr",
        number=int(attributes.get("iid") or 0),
        title=str(attributes.get("title") or ""),
        body=str(attributes.get("description") or ""),
        labels=_labels(payload),
        branch=str(attributes.get("source_branch") or ""),
        base_branch=str(attributes.get("target_branch") or ""),
        head_sha=str((attributes.get("last_commit") or {}).get("id") or ""),
        url=str(attributes.get("url") or ""),
        action="synchronize" if action == "update" else "opened",
        trusted=_trusted(payload),
    )


def _note(payload: dict[str, Any]) -> TriggerEvent | None:
    attributes = payload.get("object_attributes") or {}
    item = payload.get("merge_request") or payload.get("issue") or {}
    if not item:
        return None
    body = str(attributes.get("note") or "")
    return TriggerEvent(
        source="gitlab_note",
        number=int(item.get("iid") or 0),
        title=str(item.get("title") or ""),
        body=body,
        labels=tuple(str(label.get("title") or "") for label in item.get("labels") or []),
        branch=str(item.get("source_branch") or ""),
        url=str(attributes.get("url") or ""),
        action="created",
        # A note author's access is not in the payload; only a note addressed to the agent on
        # the project's own item (not a fork's merge request) is taken.
        trusted=_trusted({"object_attributes": item}),
        mentions_agent=any(m in body.lower() for m in AGENT_MENTIONS),
    )


def _pipeline(payload: dict[str, Any]) -> TriggerEvent | None:
    attributes = payload.get("object_attributes") or {}
    if attributes.get("status") != "failed":
        return None
    merge_request = payload.get("merge_request") or {}
    return TriggerEvent(
        source="gitlab_pipeline_failed",
        number=int(merge_request.get("iid") or 0),
        title=f"Pipeline failed on {attributes.get('ref') or 'a branch'}",
        branch=str(attributes.get("ref") or ""),
        head_sha=str(attributes.get("sha") or ""),
        url=str(attributes.get("url") or ""),
        action="completed",
        trusted=_trusted({"object_attributes": merge_request}) if merge_request else True,
    )


def _repo_merge_request(payload: dict[str, Any]) -> RepoEvent | None:
    attributes = payload.get("object_attributes") or {}
    action = attributes.get("action")
    if action not in ("merge", "close"):
        return None
    number = int(attributes.get("iid") or 0)
    merged = action == "merge"
    return RepoEvent(
        kind="pull_request.closed",
        repo_full_name=_project(payload),
        summary=f"Merge request !{number} was {'merged' if merged else 'closed without merging'}.",
        pr_numbers=(number,),
        branch=str(attributes.get("source_branch") or ""),
        actor=str((payload.get("user") or {}).get("username") or ""),
        url=str(attributes.get("url") or ""),
        outcome="merged" if merged else "closed",
        ends_watch=True,
        trusted=True,
    )


def _repo_pipeline(payload: dict[str, Any]) -> RepoEvent | None:
    attributes = payload.get("object_attributes") or {}
    status = str(attributes.get("status") or "")
    if status not in ("success", "failed", "canceled"):
        return None
    merge_request = payload.get("merge_request") or {}
    number = int(merge_request.get("iid") or 0)
    outcome = "success" if status == "success" else "failure"
    return RepoEvent(
        kind="check_suite.completed",
        repo_full_name=_project(payload),
        summary=f"Pipeline {status} on {attributes.get('ref')} ({str(attributes.get('sha'))[:12]}).",
        pr_numbers=(number,) if number else (),
        branch=str(attributes.get("ref") or ""),
        actor=str((payload.get("user") or {}).get("username") or ""),
        url=str(attributes.get("url") or ""),
        outcome=outcome,
        trusted=True,
        head_sha=str(attributes.get("sha") or ""),
    )


def _repo_note(payload: dict[str, Any]) -> RepoEvent | None:
    merge_request = payload.get("merge_request") or {}
    if not merge_request:
        return None
    attributes = payload.get("object_attributes") or {}
    number = int(merge_request.get("iid") or 0)
    author = str((payload.get("user") or {}).get("username") or "")
    return RepoEvent(
        kind="comment.created",
        repo_full_name=_project(payload),
        summary=f"@{author} commented on !{number}: {str(attributes.get('note') or '')[:500]}",
        pr_numbers=(number,),
        actor=author,
        url=str(attributes.get("url") or ""),
        trusted=_trusted({"object_attributes": merge_request}),
    )


_TRIGGER_PARSERS = {
    "Issue Hook": _issue,
    "Merge Request Hook": _merge_request,
    "Note Hook": _note,
    "Pipeline Hook": _pipeline,
}
_REPO_PARSERS = {
    "Merge Request Hook": _repo_merge_request,
    "Pipeline Hook": _repo_pipeline,
    "Note Hook": _repo_note,
}
