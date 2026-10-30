"""Automation contracts: triggers that start sessions, and webhooks that report what happened.

A **trigger** belongs to an organization: which events start (or wake) a session, with which
agent, permission mode, model tier and sandbox type, and how (``work`` on the task or ``review``
a pull request). Every source (GitHub, GitLab, a schedule, the API, Jira, Linear) is parsed into
one ``TriggerEvent``; ``matches`` decides, so no source has rules of its own.

An **outbound webhook** is an organization's URL that receives signed reports of turn and pull
request outcomes (``OUTBOUND_EVENTS``).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Protocol

TRIGGER_SOURCES: tuple[str, ...] = (
    "github_issue_label",
    "github_issue_comment",
    "github_pr",
    "github_pr_review_comment",
    "github_workflow_failed",
    "gitlab_issue_label",
    "gitlab_mr",
    "gitlab_note",
    "gitlab_pipeline_failed",
    "schedule",
    "api",
    "jira_issue",
    "linear_issue",
)
# ``work``: do the task the event describes. ``review``: review the pull request and post one
# review with inline comments (only for pull request sources).
TRIGGER_MODES: tuple[str, ...] = ("work", "review")
REVIEW_SOURCES = frozenset({"github_pr", "gitlab_mr"})
COMMENT_SOURCES = frozenset({"github_issue_comment", "github_pr_review_comment", "gitlab_note"})
MODEL_TIERS: tuple[str, ...] = ("", "fast", "default", "frontier")

OUTBOUND_EVENTS: tuple[str, ...] = ("turn.completed", "turn.failed", "pr.opened", "budget.blocked")


@dataclass(frozen=True)
class TriggerFilter:
    """Which events of a source a trigger takes; an empty list takes any."""

    # ``owner/name`` glob patterns (``acme/*``).
    repositories: tuple[str, ...] = ()
    # Any of these labels (issue or pull request labels; Jira and Linear labels).
    labels: tuple[str, ...] = ()
    # Branch glob patterns (the pull request's head branch, or the failed run's branch).
    branches: tuple[str, ...] = ()


@dataclass(frozen=True)
class Trigger:
    id: str
    org_id: str
    name: str
    source: str
    filter: TriggerFilter = field(default_factory=TriggerFilter)
    # The agent that answers (empty: the organization's default agent).
    agent_id: str = ""
    # Empty: the organization's default (and never more than it allows).
    permission_mode: str = ""
    model_tier: str = ""
    sandbox_type: str = ""
    mode: str = "work"
    # After the session's pull request: wake it on a failed check or a review comment to fix
    # what is clear (bounded by the session's continuation limits).
    auto_fix: bool = False
    # Extra instructions placed before the event's text.
    instructions: str = ""
    # ``schedule``: a cron expression (UTC).
    schedule: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class TriggerEvent:
    """One event from any source, in the terms triggers match on."""

    source: str
    # The source's id for the delivery (GitHub's X-GitHub-Delivery, ...): one session per event,
    # however many replicas receive it.
    delivery_id: str = ""
    repository: str = ""
    # The issue, pull request (merge request) or ticket number; 0 when the event has none.
    number: int = 0
    # A ticket key (Jira ``ABC-12``, Linear ``ENG-3``); empty for repositories.
    key: str = ""
    title: str = ""
    body: str = ""
    labels: tuple[str, ...] = ()
    branch: str = ""
    base_branch: str = ""
    head_sha: str = ""
    actor: str = ""
    url: str = ""
    # The event's own verb ("labeled", "synchronize", "created", ...).
    action: str = ""
    # The forge's installation or project the event came through (maps it to an organization).
    installation_id: int | None = None
    # The author is trusted by the source (a repository insider): only then may it start work.
    trusted: bool = False
    # A comment that addresses the agent ("@rocketchat", "/agent", ...).
    mentions_agent: bool = False


def _any_glob(value: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(value.lower(), pattern.lower()) for pattern in patterns)


def matches(trigger: Trigger, event: TriggerEvent) -> bool:
    """Whether ``trigger`` takes ``event``: same source, enabled, and every filter that is set
    accepts it. An untrusted author never starts work, whatever the filter."""
    if not trigger.enabled or trigger.source != event.source or not event.trusted:
        return False
    rules = trigger.filter
    if rules.repositories and not _any_glob(event.repository, rules.repositories):
        return False
    if rules.labels and not {label.lower() for label in event.labels} & {
        label.lower() for label in rules.labels
    }:
        return False
    if rules.branches and not _any_glob(event.branch, rules.branches):
        return False
    # A comment starts work when it addresses the agent, or when the trigger is about a label
    # the item carries (every comment on an ``ai-fix`` issue).
    return event.source not in COMMENT_SOURCES or event.mentions_agent or bool(rules.labels)


@dataclass(frozen=True)
class OutboundWebhook:
    id: str
    org_id: str
    url: str
    events: tuple[str, ...]
    # Fingerprint of the signing secret; the secret itself stays sealed on the platform.
    secret_fingerprint: str = ""
    enabled: bool = True
    description: str = ""


class OutboundWebhookSender(Protocol):
    """Reports an organization's outcomes to its webhooks (signed, retried, never blocking)."""

    async def emit(self, org_id: str, event: str, payload: dict[str, object]) -> None: ...
