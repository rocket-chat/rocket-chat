"""Unit tests for Slack abuse guardrails."""

from slack_assistant.admission import SlackAdmissionMixin
from slack_assistant.config import SlackAssistantSettings
from slack_assistant.guardrails import (
    DEFAULT_BLOCKED_TOOLS,
    FailureTracker,
    parse_tool_csv,
    tool_enforcement_message,
)


def test_parse_tool_csv_ignores_blank_entries() -> None:
    assert parse_tool_csv("a, b ,,c") == frozenset({"a", "b", "c"})
    assert parse_tool_csv("") == frozenset()
    assert parse_tool_csv(None) == frozenset()


class _Admission(SlackAdmissionMixin):
    def __init__(self, allowed: str = "*", blocked: str = "") -> None:
        self.settings = SlackAssistantSettings(
            slack_allowed_tools=allowed, slack_blocked_tools=blocked
        )


def test_a_wildcard_stays_unrestricted_so_turn_time_tools_are_kept() -> None:
    """MCP tools only exist once the turn starts: a wildcard must not become a list."""
    admission = _Admission("*", "git_revert,git_open_pr")

    assert admission._slack_whitelisted_tools(base=None) is None
    assert admission._slack_whitelisted_tools(base=["*"]) is None
    assert admission._slack_tool_selection()[1] == frozenset({"git_revert", "git_open_pr"})


def test_explicit_allowlists_are_intersected_and_blocked_wins() -> None:
    admission = _Admission("file_read,git_revert,web_search", "git_revert")

    assert admission._slack_whitelisted_tools(base=None) == ["file_read", "web_search"]
    assert admission._slack_whitelisted_tools(base=["file_read", "bash_exec"]) == ["file_read"]
    assert admission._slack_whitelisted_tools(base=["*"]) == ["file_read", "web_search"]


def test_an_agent_list_under_a_slack_wildcard_is_kept_minus_the_blocklist() -> None:
    admission = _Admission("*", "")

    assert admission._slack_whitelisted_tools(base=["file_read", "git_revert"]) == ["file_read"]
    assert admission._slack_tool_selection()[1] == frozenset(DEFAULT_BLOCKED_TOOLS)


def test_the_organizations_list_replaces_the_deployment_one() -> None:
    admission = _Admission("*", "")

    assert admission._slack_whitelisted_tools(org_allowed=("web_search",)) == ["web_search"]


def test_enforcement_message_names_the_blocked_tool() -> None:
    message = tool_enforcement_message("git_revert")
    assert "git_revert" in message
    assert "not permitted" in message


def test_failure_tracker_blocks_then_recovers_after_cooldown() -> None:
    tracker = FailureTracker(threshold=2, cooldown_seconds=300)
    now = 1000.0
    assert tracker.is_blocked("team", now) is False
    tracker.record_failure("team", now)
    assert tracker.is_blocked("team", now) is False
    tracker.record_failure("team", now)
    assert tracker.is_blocked("team", now) is True
    assert tracker.retry_after_seconds("team", now) == 300

    # Inside the cooldown the breaker still blocks; past it, the scope gets a clean slate.
    assert tracker.is_blocked("team", now + 299) is True
    assert tracker.is_blocked("team", now + 301) is False
    assert tracker.retry_after_seconds("team", now + 301) == 0


def test_failure_tracker_success_resets_the_streak() -> None:
    tracker = FailureTracker(threshold=3, cooldown_seconds=60)
    tracker.record_failure("team", 0.0)
    tracker.record_failure("team", 0.0)
    tracker.record_success("team")
    tracker.record_failure("team", 0.0)
    assert tracker.is_blocked("team", 0.0) is False


def test_failure_tracker_is_bounded() -> None:
    tracker = FailureTracker(threshold=1, cooldown_seconds=1, max_entries=3)
    for index in range(10):
        tracker.record_failure(f"team-{index}", float(index))
    assert len(tracker._streaks) == 3


def test_failure_tracker_disabled_when_threshold_zero() -> None:
    tracker = FailureTracker(threshold=0, cooldown_seconds=60)
    tracker.record_failure("team", 0.0)
    assert tracker.is_blocked("team", 0.0) is False
