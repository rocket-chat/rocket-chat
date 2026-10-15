"""Every branch the agent publishes is an audit event of its own, with what was published."""

from __future__ import annotations

from typing import Any

import pytest
from api.services.audit import AuditService

from specifications.interfaces.git import PublishResult, PublishSpec
from tests.turn_harness import run_turn


class _Engine:
    """Publishes successfully or refuses, without a repository."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def publish_branch(self, session_id: str, spec: PublishSpec) -> PublishResult:
        if self.fail:
            raise RuntimeError("No GitHub credential can write to acme/api.")
        return PublishResult("acme/api", "feat/x", "abc123def4567", "git_push", commits=2)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    real = AuditService.record

    async def record(self: AuditService, event_type: str, **kwargs: Any) -> Any:
        events.append({"event_type": event_type, **kwargs})
        return await real(self, event_type, **kwargs)

    monkeypatch.setattr(AuditService, "record", record)
    return events


async def test_a_publication_is_audited_with_its_summary(recorded: list[dict[str, Any]]) -> None:
    await run_turn("org_pub", script=[("git_push", {})], git_engine=_Engine())

    published = [e for e in recorded if e["event_type"] == "git_publish"]
    assert len(published) == 1
    assert published[0]["tool_name"] == "git_push"
    assert published[0]["result_summary"].startswith("Pushed acme/api@feat/x")


async def test_a_refused_publication_is_not_a_publication(recorded: list[dict[str, Any]]) -> None:
    await run_turn("org_pub_fail", script=[("git_push", {})], git_engine=_Engine(fail=True))

    assert not [e for e in recorded if e["event_type"] == "git_publish"]
    assert [e for e in recorded if e["event_type"] == "tool_call"], "the call itself still is"
