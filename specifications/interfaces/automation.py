"""Automation contracts: missions, the events that start them, and where their results go.

A **mission** is an agent run the platform starts on its own: *when* (one to three triggers: a
cron schedule, a forge or tracker event, an inbound webhook of its own, or a person pressing
*Run now*), *who* (an agent, permission mode, model tier, sandbox type), *what* (instructions)
and *where the result goes* (deliveries). It is owned like any object (a member, a team or the
organization). Every source is parsed into one ``TriggerEvent``; ``matches`` decides whether a
mission's trigger takes it, so no source has rules of its own.

A **delivery** is published by the platform after the run, never by the agent: the agent hands
its result to the ``deliver`` tool, checked against the delivery's schema
(``delivery_schema``), and the platform posts it with the mission's own rights. A run is always
a session too, which is the audit trail of what it did.

An **outbound webhook** is an organization's URL that receives signed reports of turn and pull
request outcomes (``OUTBOUND_EVENTS``), whatever started them.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

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
    "webhook",
    "api",
    "jira_issue",
    "linear_issue",
)
# Sources a mission's trigger names nothing else for: they fire for the mission itself.
OWN_SOURCES = frozenset({"schedule", "webhook", "api"})
MAX_TRIGGERS = 3
# ``work``: do the task the event describes. ``review``: review the pull request (only for pull
# request sources); its result is a review, delivered by the platform.
MISSION_MODES: tuple[str, ...] = ("work", "review")
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
class MissionTrigger:
    """One way a mission starts: a source and the filter its events must pass."""

    source: str
    filter: TriggerFilter = field(default_factory=TriggerFilter)
    # ``schedule``: a five-field cron expression, read in ``timezone``.
    schedule: str = ""
    timezone: str = "UTC"


DeliveryKind = Literal["session", "chat", "webhook", "github_review", "github_comment"]
DELIVERY_KINDS: tuple[DeliveryKind, ...] = (
    "session",
    "chat",
    "webhook",
    "github_review",
    "github_comment",
)
# Deliveries whose result is text: without a ``deliver`` call the run's answer is used.
TEXT_DELIVERIES = frozenset({"chat", "github_comment"})
# Deliveries that post on the item the event is about (a pull request, an issue).
ITEM_DELIVERIES = frozenset({"github_review", "github_comment"})
MAX_DELIVERIES = 4
MAX_OUTPUT_SCHEMA_CHARS = 4000


@dataclass(frozen=True)
class Delivery:
    """Where a run's result goes. ``session`` is every run's own record and needs nothing."""

    kind: DeliveryKind
    # ``chat``: the Slack bot that posts and the channel it posts in.
    bot_id: str = ""
    channel: str = ""
    # ``webhook``: the https URL the signed result is sent to; its secret stays sealed.
    url: str = ""
    # ``webhook``: the JSON Schema the result must follow (an object); empty: ``{summary}``.
    output_schema: dict[str, Any] = field(default_factory=dict)


_TEXT = {"type": "string", "minLength": 1, "maxLength": 20000}
NATIVE_SCHEMAS: dict[str, dict[str, Any]] = {
    "chat": {
        "type": "object",
        "properties": {"text": {**_TEXT, "maxLength": 3000}},
        "required": ["text"],
        "additionalProperties": False,
    },
    "github_comment": {
        "type": "object",
        "properties": {"body": _TEXT},
        "required": ["body"],
        "additionalProperties": False,
    },
    "github_review": {
        "type": "object",
        "properties": {
            "event": {"enum": ["comment", "approve", "request_changes"]},
            "body": _TEXT,
            "comments": {
                "type": "array",
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "minLength": 1},
                        "line": {"type": "integer", "minimum": 1},
                        "body": {**_TEXT, "maxLength": 4000},
                    },
                    "required": ["path", "line", "body"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["event", "body"],
        "additionalProperties": False,
    },
}
DEFAULT_WEBHOOK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": _TEXT},
    "required": ["summary"],
}


def delivery_schema(deliveries: tuple[Delivery, ...]) -> dict[str, Any] | None:
    """The schema of the ``deliver`` tool's ``output`` for these deliveries: one property per
    delivery that needs a result, named by its kind; ``None`` when none does (session only)."""
    properties: dict[str, Any] = {}
    for delivery in deliveries:
        if delivery.kind == "webhook":
            properties["webhook"] = delivery.output_schema or DEFAULT_WEBHOOK_SCHEMA
        elif delivery.kind in NATIVE_SCHEMAS:
            properties[delivery.kind] = NATIVE_SCHEMAS[delivery.kind]
    if not properties:
        return None
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


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


MissionRunStatus = Literal["running", "completed", "failed", "delivered", "delivery_failed"]


@dataclass
class Mission:
    """A mission as stored. ``scope``/``user_id``/``team_id`` is its owner (ownership.py)."""

    id: str
    org_id: str
    name: str
    instructions: str
    scope: str = "user"
    user_id: str | None = None
    team_id: str | None = None
    triggers: tuple[MissionTrigger, ...] = ()
    deliveries: tuple[Delivery, ...] = (Delivery("session"),)
    # Empty: the owner's default agent (the reviewer for ``review``).
    agent_id: str = ""
    permission_mode: str = ""
    model_tier: str = ""
    sandbox_type: str = ""
    mode: str = "work"
    # After the run's pull request: wake it on a failed check or a review comment to fix what is
    # clear (bounded by the session's continuation limits).
    auto_fix: bool = False
    git_repo: str = ""
    git_branch: str = ""
    enabled: bool = True
    # Who its runs are: the owner for a personal mission; a service member of the mission for a
    # team's or the organization's (``mission_<id>``), never the person who triggered it.
    run_as: str = ""
    # Fingerprint of the inbound webhook's signing secret (sealed on the platform).
    webhook_fingerprint: str = ""
    next_run_at: int | None = None
    last_run_at: int | None = None
    last_session_id: str | None = None
    last_status: str | None = None
    created_by: str = ""
    created_at: int = 0
    updated_at: int = 0


@dataclass(frozen=True)
class MissionRun:
    """One run: the event that started it, its session, and how each delivery went."""

    id: str
    org_id: str
    mission_id: str
    session_id: str
    source: str
    title: str = ""
    status: str = "running"
    # [{kind, status, error}] once the run ends.
    deliveries: tuple[dict[str, str], ...] = ()
    started_at: int = 0
    finished_at: int | None = None


def _any_glob(value: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(value.lower(), pattern.lower()) for pattern in patterns)


def matches(trigger: MissionTrigger, event: TriggerEvent) -> bool:
    """Whether ``trigger`` takes ``event``: same source, and every filter that is set accepts
    it. An untrusted author never starts work, whatever the filter."""
    if trigger.source != event.source or not event.trusted:
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
