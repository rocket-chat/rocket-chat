"""Unit tests for the wait_for tool and durable waiting lifecycle (Item F1)."""

from __future__ import annotations

from typing import Any

import pytest
from agent_core.tools.registry import ToolRegistry
from sandbox_driver.fake_driver import FakeSandboxDriver

from tests.turn_harness import run_turn


@pytest.mark.asyncio
async def test_wait_for_tool_callback() -> None:
    session_id = "s_wait_tool"
    registry = ToolRegistry(driver=FakeSandboxDriver(), session_id=session_id)

    recorded_wait: list[tuple[str, str, int | None]] = []

    async def fake_wait_cb(target: str, identifier: str, timeout_seconds: int | None) -> str:
        recorded_wait.append((target, identifier, timeout_seconds))
        return f"Wrote wait {target}"

    registry._on_wait_for_callback = fake_wait_cb

    result = await registry.call_tool(
        "wait_for", {"target": "ci_status", "identifier": "org/repo#42", "timeout_seconds": 300}
    )
    assert result == "Wrote wait ci_status"
    assert len(recorded_wait) == 1
    assert recorded_wait[0] == ("ci_status", "org/repo#42", 300)


@pytest.mark.asyncio
async def test_orchestrator_terminates_turn_on_wait_for() -> None:
    outcome = await run_turn(
        "org_wait",
        script=[("wait_for", {"target": "time", "identifier": "5"})],
    )

    # Status should contain waiting event and final status should be waiting
    status_events = outcome.events_of("status_changed")
    waiting_statuses = [e for e in status_events if e.payload.get("status") == "waiting"]
    assert len(waiting_statuses) >= 1
    # Check that wait_started event was also emitted
    wait_started_events = outcome.events_of("wait_started")
    assert len(wait_started_events) >= 1
    assert wait_started_events[0].payload.get("wait_type") == "time"
    assert "time" in wait_started_events[0].payload.get("reason", "")


@pytest.mark.asyncio
async def test_check_github_ci_poll_scenarios(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    from api.services.git_credentials import GitCredentialBundle
    from api.turns.callbacks import _check_github_ci_poll

    ctx = MagicMock()
    bundle = GitCredentialBundle(
        token="ghp_test",
        mode="member",
        author_name="Tester",
        author_email="test@example.com",
        user_token="ghp_test",
    )

    # Mock _github_read_access to return a mock GithubAccess
    from specifications.interfaces.agent import GithubAccess

    access = GithubAccess("ghp_test", "https://api.github.com", "user")

    async def fake_access_fn(repo: str) -> GithubAccess:
        return access

    monkeypatch.setattr("api.turns.callbacks._github_read_access", lambda _c, _b: fake_access_fn)

    # 1. Any check failed -> should wake immediately
    async def mock_gh_request_fail(
        acc: Any, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        if "pulls" in path:
            return {"head": {"sha": "sha123"}}
        if "check-runs" in path:
            return {
                "check_runs": [
                    {"name": "lint", "status": "completed", "conclusion": "success"},
                    {"name": "test-suite", "status": "completed", "conclusion": "failure"},
                ]
            }
        return {"statuses": []}

    monkeypatch.setattr("agent_core.tools.github_client.request", mock_gh_request_fail)
    status, summary, should_wake = await _check_github_ci_poll(ctx, bundle, "acme/repo", 42)
    assert should_wake is True
    assert status == "failed"
    assert "test-suite" in summary

    # 2. In progress with 0 failures -> do NOT wake
    async def mock_gh_request_running(
        acc: Any, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        if "pulls" in path:
            return {"head": {"sha": "sha123"}}
        if "check-runs" in path:
            return {
                "check_runs": [
                    {"name": "lint", "status": "completed", "conclusion": "success"},
                    {"name": "test-suite", "status": "in_progress", "conclusion": None},
                ]
            }
        return {"statuses": []}

    monkeypatch.setattr("agent_core.tools.github_client.request", mock_gh_request_running)
    status, summary, should_wake = await _check_github_ci_poll(ctx, bundle, "acme/repo", 42)
    assert should_wake is False
    assert status == "in_progress"

    # 3. All completed and green -> wake
    async def mock_gh_request_green(
        acc: Any, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        if "pulls" in path:
            return {"head": {"sha": "sha123"}}
        if "check-runs" in path:
            return {
                "check_runs": [
                    {"name": "lint", "status": "completed", "conclusion": "success"},
                    {"name": "test-suite", "status": "completed", "conclusion": "success"},
                ]
            }
        return {"statuses": []}

    monkeypatch.setattr("agent_core.tools.github_client.request", mock_gh_request_green)
    status, summary, should_wake = await _check_github_ci_poll(ctx, bundle, "acme/repo", 42)
    assert should_wake is True
    assert status == "completed"
    assert "2 CI check(s) passed" in summary


def _github(check_runs: list[dict[str, Any]], mergeable_state: str = "clean") -> Any:
    async def request(acc: Any, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if "pulls" in path:
            return {"head": {"sha": "sha12345678"}, "mergeable_state": mergeable_state}
        if "check-runs" in path:
            return {"check_runs": check_runs}
        return {"statuses": []}

    return request


@pytest.mark.asyncio
async def test_waiting_on_settled_or_absent_ci_answers_at_once_instead_of_parking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent waited 30+ minutes on CI that had already failed, and on pull requests where
    CI never runs (no checks at all, or a branch behind its base)."""
    from unittest.mock import MagicMock

    from api.turns import callbacks
    from api.turns.callbacks import _ci_already_settled

    from specifications.interfaces.agent import GithubAccess

    async def access(repo: str) -> GithubAccess:
        return GithubAccess("ghp_test", "https://api.github.com", "user")

    monkeypatch.setattr("api.turns.callbacks._github_read_access", lambda _c, _b: access)
    monkeypatch.setattr(callbacks, "NO_CHECKS_GRACE_SECONDS", 0.0)
    bundle = MagicMock()

    failed = [{"name": "build", "status": "completed", "conclusion": "failure"}]
    monkeypatch.setattr("agent_core.tools.github_client.request", _github(failed))
    answer = await _ci_already_settled(MagicMock(), bundle, "acme/api", 7)
    assert answer is not None and "CI failed" in answer and "No wait was registered" in answer

    monkeypatch.setattr("agent_core.tools.github_client.request", _github([], "behind"))
    answer = await _ci_already_settled(MagicMock(), bundle, "acme/api", 7)
    assert answer is not None and "No CI check" in answer and "behind its base" in answer

    running = [{"name": "build", "status": "in_progress", "conclusion": None}]
    monkeypatch.setattr("agent_core.tools.github_client.request", _github(running))
    assert await _ci_already_settled(MagicMock(), bundle, "acme/api", 7) is None
