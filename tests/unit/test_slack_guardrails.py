"""Unit tests for Slack abuse guardrails."""

from slack_assistant.guardrails import (
    DEFAULT_BLOCKED_TOOLS,
    FailureTracker,
    parse_tool_csv,
    resolve_allowed_tools,
    tool_enforcement_message,
)


def test_parse_tool_csv_ignores_blank_entries() -> None:
    assert parse_tool_csv("a, b ,,c") == frozenset({"a", "b", "c"})
    assert parse_tool_csv("") == frozenset()
    assert parse_tool_csv(None) == frozenset()


def test_wildcard_allowlist_subtracts_the_blocklist() -> None:
    registered = ["file_read", "bash_exec", "git_revert", "git_open_pr", "web_search"]
    allowed, blocked = resolve_allowed_tools(
        registered=registered,
        allowed_raw="*",
        blocked_raw="git_revert,git_open_pr",
    )
    assert blocked == frozenset({"git_revert", "git_open_pr"})
    # The wildcard is expanded to an explicit list so the execution layer can enforce it.
    assert allowed is not None
    assert "git_revert" not in allowed
    assert "git_open_pr" not in allowed
    assert set(allowed) == {"file_read", "bash_exec", "web_search"}


def test_explicit_allowlist_is_intersected_and_blocked_wins() -> None:
    registered = ["file_read", "bash_exec", "git_revert", "web_search"]
    allowed, _ = resolve_allowed_tools(
        registered=registered,
        allowed_raw="file_read,git_revert",
        blocked_raw="git_revert",
    )
    assert allowed == ["file_read"]


def test_default_blocklist_blocks_destructive_git_tools() -> None:
    registered = ["git_revert", "git_open_pr", "file_read"]
    allowed, blocked = resolve_allowed_tools(registered=registered, allowed_raw="*", blocked_raw="")
    assert blocked == frozenset(DEFAULT_BLOCKED_TOOLS)
    assert allowed == ["file_read"]


def test_unknown_catalog_degrades_to_advisory_only() -> None:
    allowed, blocked = resolve_allowed_tools(
        registered=[], allowed_raw="*", blocked_raw="git_revert"
    )
    assert allowed is None
    assert blocked == frozenset({"git_revert"})


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
