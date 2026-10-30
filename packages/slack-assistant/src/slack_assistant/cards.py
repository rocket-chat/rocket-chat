"""The cards a turn leaves in its thread: the pull request it opened, and its summary.

The pull request card is posted once when ``git_open_pr`` succeeds and updated in place as CI
reports (the platform calls ``SlackAssistant.update_pull_request_cards`` on each GitHub event of
the pull request). The summary card closes every answer: how the turn ended, what it changed,
its time and cost, and buttons to open the session or retry a failed request.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any

RETRY_ACTION_ID = "retry_request"
FOLLOWUP_ACTION_ID = "run_followup"
OPEN_SESSION_ACTION_ID = "open_session"
# Slack caps a button's value at 2,000 characters and its label at 75.
_VALUE_CHARS = 2000
_LABEL_CHARS = 75
# Session metadata key holding the thread's pull request cards (bounded, newest last).
PR_CARDS_KEY = "slack_pr_cards"
MAX_PR_CARDS = 10

_PULL_REQUEST_URL = re.compile(r"https://[^/\s]+/([\w.-]+/[\w.-]+)/pull/(\d+)")
_PUBLISHED_BRANCH = re.compile(r"(?:Pushed|to) ([\w.-]+/[\w.-]+)@([\w./-]+?)[ .]")

_CI_LABELS = {
    "pending": ":hourglass_flowing_sand: CI running",
    "success": ":white_check_mark: CI passed",
    "failure": ":x: CI failed",
    "merged": ":purple_heart: Merged",
    "closed": ":no_entry_sign: Closed",
}


@dataclass(frozen=True)
class PullRequestCard:
    repository: str
    number: int
    url: str
    branch: str = ""
    files: int = 0
    additions: int = 0
    deletions: int = 0
    ci: str = "pending"
    channel_id: str = ""
    ts: str = ""

    def as_metadata(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_metadata(cls, raw: Any) -> PullRequestCard | None:
        if not isinstance(raw, dict):
            return None
        try:
            return cls(**{key: raw[key] for key in cls.__dataclass_fields__ if key in raw})
        except TypeError:
            return None


def pull_request_of(result: str) -> tuple[str, int, str] | None:
    """``(owner/name, number, url)`` of the pull request a ``git_open_pr`` result opened."""
    if not result.startswith("Pull request opened") or not (
        match := _PULL_REQUEST_URL.search(result)
    ):
        return None
    return match.group(1), int(match.group(2)), match.group(0)


def published_branch_of(result: str) -> str:
    """The branch a ``git_push`` result published (empty when it published nothing)."""
    match = _PUBLISHED_BRANCH.search(result)
    return match.group(2) if match else ""


def ci_state_of(kind: str, outcome: str) -> str | None:
    """The card's CI state after a GitHub event, or None when the event does not change it."""
    if kind == "pull_request.closed":
        return "merged" if outcome == "merged" else "closed"
    if kind in ("check_suite.completed", "workflow_run.completed", "check_run.completed"):
        if outcome in ("success", "neutral", "skipped"):
            return "success"
        if outcome in ("failure", "timed_out", "cancelled", "action_required"):
            return "failure"
    return None


def with_ci(card: PullRequestCard, state: str) -> PullRequestCard:
    return replace(card, ci=state)


def build_pull_request_card(card: PullRequestCard, link_url: str | None) -> list[dict[str, Any]]:
    """PR link, branch, files changed and CI state, with buttons to the PR and the session."""
    fields = [{"type": "mrkdwn", "text": f"*CI*\n{_CI_LABELS.get(card.ci, card.ci)}"}]
    if card.branch:
        fields.insert(0, {"type": "mrkdwn", "text": f"*Branch*\n`{card.branch}`"})
    if card.files:
        changes = f"{card.files} file{'s' if card.files != 1 else ''} (+{card.additions} -{card.deletions})"
        fields.append({"type": "mrkdwn", "text": f"*Changes*\n{changes}"})
    buttons: list[dict[str, Any]] = [
        _url_button("Open pull request", card.url, "open_pull_request")
    ]
    if link_url:
        buttons.append(_url_button("Open in Rocket Chat", link_url, OPEN_SESSION_ACTION_ID))
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":git-pull-request: *Pull request* <{card.url}|{card.repository}#{card.number}>",
            },
            "fields": fields,
        },
        {"type": "actions", "elements": buttons},
    ]


@dataclass(frozen=True)
class TurnSummary:
    """How a turn ended, for its summary card."""

    status: str  # completed, waiting, failed, stopped
    # How long the turn ran, when the reply streamed it (None: not known here).
    elapsed_seconds: int | None = None
    changes: str = ""
    cost_usd: float = 0.0
    pull_request_url: str = ""
    wait_notice: str = ""


_STATUS_LABELS = {
    "completed": ":white_check_mark: Done",
    "waiting": ":hourglass_flowing_sand: Waiting",
    "failed": ":warning: Failed",
    "stopped": ":black_square_for_stop: Stopped",
}


def build_summary_card(
    summary: TurnSummary,
    *,
    link_url: str | None,
    retry_value: str | None = None,
) -> list[dict[str, Any]]:
    """One context line (status · time · changes · cost · PR) and the session's buttons."""
    label = _STATUS_LABELS.get(summary.status, summary.status)
    if summary.elapsed_seconds is None:
        parts = [label]
    elif summary.status == "completed":
        parts = [f"{label} in {_duration(summary.elapsed_seconds)}"]
    else:
        parts = [label, _duration(summary.elapsed_seconds)]
    if summary.changes:
        parts.append(summary.changes)
    if summary.cost_usd >= 0.005:
        parts.append(f"${summary.cost_usd:.2f}")
    if summary.pull_request_url:
        parts.append(f"<{summary.pull_request_url}|pull request>")
    blocks: list[dict[str, Any]] = []
    if summary.wait_notice:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": summary.wait_notice[:2900]}}
        )
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(parts)}]})
    buttons: list[dict[str, Any]] = []
    if link_url:
        buttons.append(_url_button("Open in Rocket Chat", link_url, OPEN_SESSION_ACTION_ID))
    if retry_value:
        buttons.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Retry"},
                "action_id": RETRY_ACTION_ID,
                "value": retry_value[:_VALUE_CHARS],
            }
        )
    if buttons:
        blocks.append({"type": "actions", "elements": buttons})
    return blocks


def build_followup_blocks(suggestion: str, session_id: str) -> list[dict[str, Any]]:
    """A suggested next request as a button that sends it (only its requester may)."""
    label = suggestion if len(suggestion) <= _LABEL_CHARS else suggestion[: _LABEL_CHARS - 1] + "…"
    return [
        {
            "type": "actions",
            "block_id": f"followup:{session_id}"[:255],
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": label},
                    "action_id": FOLLOWUP_ACTION_ID,
                    "value": suggestion[:_VALUE_CHARS],
                }
            ],
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "Suggested next step"}]},
    ]


def wait_notice(wait_type: str, target: str) -> str:
    """ "Waiting for CI on acme/api#123; I'll post here." for a wait the turn ended on."""
    what = {
        "ci_status": "CI",
        "review": "a review",
        "process_exit": "a background process",
        "subagent": "a subagent",
        "time": "a timer",
    }.get(wait_type, "an external event")
    where = f" on {target}" if wait_type in ("ci_status", "review") and target else ""
    return f":hourglass_flowing_sand: Waiting for {what}{where}; I'll post here."


def _duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}m {rest:02d}s" if minutes < 60 else f"{minutes // 60}h {minutes % 60:02d}m"


def _url_button(text: str, url: str, action_id: str) -> dict[str, Any]:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": text},
        "url": url,
        "action_id": action_id,
    }
