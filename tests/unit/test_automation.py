"""Automation: triggers (matching, de-duplication, review mode, auto-fix), outbound webhooks,
Jira and Linear, and the GitHub trigger parser."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from api.services import outbound_webhooks, triggers
from api.services.github_watches import GithubWatchStore, watch_callbacks
from api.services.outbound_webhooks import OutboundWebhookError, sign
from api.services.ticket_trackers import TrackerSignatureError, jira_event, linear_event
from api.services.triggers import (
    TriggerError,
    claim_delivery,
    default_triggers,
    matching_trigger,
    prompt_for,
    session_id_for,
    start_from_trigger,
    validate,
)
from git_engine.github_triggers import parse_github_trigger

from specifications.interfaces.automation import Trigger, TriggerEvent, TriggerFilter, matches


def _trigger(**overrides: Any) -> Trigger:
    return replace(Trigger(id="t1", org_id="org", name="T", source="github_pr"), **overrides)


def _event(**overrides: Any) -> TriggerEvent:
    base = TriggerEvent(
        source="github_pr",
        repository="acme/api",
        number=7,
        title="Add cache",
        branch="feat/cache",
        labels=("backend",),
        head_sha="abc123",
        trusted=True,
    )
    return replace(base, **overrides)


@pytest.fixture(autouse=True)
def _clean() -> Any:
    triggers._memory.clear()
    triggers._claimed.clear()
    outbound_webhooks.reset_memory()
    yield
    triggers._memory.clear()
    triggers._claimed.clear()
    outbound_webhooks.reset_memory()


# -- matching -----------------------------------------------------------------------------------


def test_a_trigger_takes_only_its_source_and_its_filters() -> None:
    rules = TriggerFilter(repositories=("acme/*",), labels=("backend",), branches=("feat/*",))
    trigger = _trigger(filter=rules)
    assert matches(trigger, _event())
    assert not matches(trigger, _event(repository="other/api"))
    assert not matches(trigger, _event(labels=("frontend",)))
    assert not matches(trigger, _event(branch="main"))
    assert not matches(trigger, _event(source="github_issue_label"))
    assert not matches(replace(trigger, enabled=False), _event())


def test_an_untrusted_author_never_starts_work() -> None:
    assert not matches(_trigger(), _event(trusted=False))


def test_a_comment_starts_work_when_it_addresses_the_agent_or_the_trigger_names_labels() -> None:
    comment = _event(source="github_issue_comment", labels=("ai-fix",))
    assert not matches(_trigger(source="github_issue_comment"), comment)
    assert matches(_trigger(source="github_issue_comment"), replace(comment, mentions_agent=True))
    labelled = _trigger(source="github_issue_comment", filter=TriggerFilter(labels=("ai-fix",)))
    assert matches(labelled, comment)


async def test_an_organization_without_triggers_gets_the_deployment_label() -> None:
    labelled = _event(source="github_issue_label", labels=("ai-fix",))
    assert (await matching_trigger(None, "org", labelled)) is not None
    assert (await matching_trigger(None, "org", replace(labelled, labels=("bug",)))) is None
    assert {t.source for t in default_triggers("org")} == {
        "github_issue_label",
        "github_issue_comment",
    }


async def test_configured_triggers_replace_the_defaults() -> None:
    await triggers.save_trigger(None, _trigger(id=""), "admin")
    assert (
        await matching_trigger(None, "org", _event(source="github_issue_label", labels=("ai-fix",)))
        is None
    )
    assert await matching_trigger(None, "org", _event()) is not None


@pytest.mark.parametrize(
    "overrides, error",
    [
        ({"source": "nope"}, "Unknown source"),
        ({"mode": "review", "source": "github_issue_label"}, "pull request"),
        ({"model_tier": "huge"}, "tier"),
        ({"source": "schedule", "schedule": "every day"}, "cron"),
        ({"name": " "}, "name"),
    ],
)
def test_an_invalid_trigger_is_refused(overrides: dict[str, Any], error: str) -> None:
    with pytest.raises(TriggerError, match=error):
        validate(_trigger(**overrides))


# -- de-duplication -------------------------------------------------------------------------------


async def test_a_delivery_is_handled_once() -> None:
    assert await claim_delivery(None, "org", "github", "d-1")
    assert not await claim_delivery(None, "org", "github", "d-1")
    assert await claim_delivery(None, "other", "github", "d-1")
    assert await claim_delivery(None, "org", "github", "")


# -- sessions and prompts ------------------------------------------------------------------------


def test_a_review_and_a_fix_of_one_pull_request_are_two_sessions() -> None:
    work = session_id_for(_trigger(), _event())
    review = session_id_for(_trigger(mode="review"), _event())
    assert work != review and review.startswith("review_")
    assert session_id_for(_trigger(), _event()) == work


def test_a_review_prompt_reviews_only_the_new_commits() -> None:
    prompt = prompt_for(
        _trigger(mode="review"), _event(head_sha="new"), follow_up=False, reviewed_sha="old"
    )
    assert "git diff old..new" in prompt
    assert "review_comments" in prompt and "Do not change or push code" in prompt


def test_trigger_instructions_lead_the_prompt() -> None:
    prompt = prompt_for(
        _trigger(instructions="Keep it small."),
        _event(source="github_issue_label"),
        follow_up=False,
    )
    assert prompt.index("Keep it small.") < prompt.index("Resolve acme/api#7")


def _app(store: Any) -> Any:
    state = SimpleNamespace(
        session_store=store,
        orchestrator=object(),
        event_bus=object(),
        db_manager=None,
    )
    return SimpleNamespace(state=state)


class _Store:
    def __init__(self) -> None:
        self.records: dict[str, Any] = {}

    async def get_session(self, session_id: str, *_: Any, **__: Any) -> Any:
        return self.records.get(session_id)

    async def create_session(self, record: Any) -> None:
        self.records[record.session_id] = record

    async def mutate_session(self, session_id: str, _org: str, fn: Any) -> bool:
        fn(self.records[session_id])
        return True


async def test_a_review_runs_the_reviewer_once_per_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    spawned = []
    monkeypatch.setattr(triggers, "_spawn_background_turn", lambda **kw: spawned.append(kw))
    store = _Store()
    app = _app(store)
    review = _trigger(mode="review", auto_fix=False)
    first = await start_from_trigger(app, review, _event(), "gh_ada")
    assert first.status == "created"
    record = store.records[first.session_id]
    assert record.metadata["agent_id"] == triggers.REVIEWER_AGENT_ID
    assert (await start_from_trigger(app, review, _event(), "gh_ada")).status == "ignored"
    again = await start_from_trigger(app, review, _event(head_sha="def456"), "gh_ada")
    assert again.status == "resumed" and len(spawned) == 2
    assert "git diff abc123..def456" in spawned[-1]["prompt"]


async def test_a_trigger_carries_its_agent_mode_tier_and_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned = []
    monkeypatch.setattr(triggers, "_spawn_background_turn", lambda **kw: spawned.append(kw))
    store = _Store()
    trigger = _trigger(
        agent_id="a-1", permission_mode="ask", model_tier="fast", sandbox_type="sb-1"
    )
    outcome = await start_from_trigger(_app(store), trigger, _event(), "gh_ada")
    metadata = store.records[outcome.session_id].metadata
    assert (metadata["agent_id"], metadata["permission_mode"], metadata["sandbox_id"]) == (
        "a-1",
        "ask",
        "sb-1",
    )
    assert spawned[0]["model"] == "tier:fast"


# -- auto-fix ---------------------------------------------------------------------------------------


async def test_a_trigger_session_without_auto_fix_does_not_wake_on_its_pull_request() -> None:
    GithubWatchStore._MEMORY.clear()
    follow, _ = watch_callbacks(GithubWatchStore(None), "org", "s1", "u1", may_wake=False)
    assert "be shown to you at your next turn" in await follow("acme/api", 7, None, True)
    follow, _ = watch_callbacks(GithubWatchStore(None), "org", "s2", "u1")
    assert "start a turn here" in await follow("acme/api", 7, None, True)


# -- GitHub parsing -----------------------------------------------------------------------------------


def _github(event: str, payload: dict[str, Any]) -> TriggerEvent | None:
    return parse_github_trigger(
        json.dumps(
            {"repository": {"full_name": "Acme/API"}, "sender": {"login": "ada"}, **payload}
        ).encode(),
        {"X-GitHub-Event": event, "X-GitHub-Delivery": "d-9"},
    )


def test_github_events_become_trigger_events() -> None:
    pr = _github(
        "pull_request",
        {"action": "synchronize", "pull_request": {"number": 3, "author_association": "MEMBER",
         "head": {"ref": "feat/x", "sha": "s1"}, "base": {"ref": "main"}}},
    )  # fmt: skip
    assert pr is not None and (pr.source, pr.repository, pr.branch, pr.trusted) == (
        "github_pr",
        "acme/api",
        "feat/x",
        True,
    )
    assert pr.delivery_id == "d-9"
    stranger = _github("issue_comment", {"action": "created", "issue": {"number": 1},
                       "comment": {"body": "/agent fix", "author_association": "NONE"}})  # fmt: skip
    assert stranger is not None and stranger.mentions_agent and not stranger.trusted
    failed = _github("workflow_run", {"action": "completed", "workflow_run": {"conclusion": "failure",
                     "head_branch": "main", "head_repository": {"full_name": "Acme/API"}}})  # fmt: skip
    assert failed is not None and failed.source == "github_workflow_failed" and failed.trusted
    assert _github("pull_request", {"action": "opened", "pull_request": {"draft": True}}) is None


# -- outbound webhooks ----------------------------------------------------------------------------


def test_the_signature_covers_the_timestamp_and_the_body() -> None:
    body = b'{"event":"turn.completed"}'
    expected = hmac.new(b"whsec_x", b"1700000000." + body, hashlib.sha256).hexdigest()
    assert sign("whsec_x", 1700000000, body) == f"sha256={expected}"
    assert sign("whsec_x", 1700000001, body) != sign("whsec_x", 1700000000, body)


async def test_a_webhook_url_must_be_public_https(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(OutboundWebhookError, match="https"):
        await outbound_webhooks.create_webhook(
            None, "org", "http://example.com/h", ["turn.completed"]
        )
    with pytest.raises(OutboundWebhookError):
        await outbound_webhooks.create_webhook(
            None, "org", "https://127.0.0.1/h", ["turn.completed"]
        )
    with pytest.raises(OutboundWebhookError, match="Events"):
        await outbound_webhooks.create_webhook(None, "org", "https://example.com/h", ["nope"])


async def _hook(
    monkeypatch: pytest.MonkeyPatch, events: list[str]
) -> outbound_webhooks.CreatedWebhook:
    async def public(url: str) -> Any:
        return SimpleNamespace(host="example.com", address="93.184.216.34")

    monkeypatch.setattr(outbound_webhooks, "resolve_public_target", public)
    return await outbound_webhooks.create_webhook(None, "org", "https://example.com/h", events)


async def test_a_report_is_signed_sent_and_retried_with_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await _hook(monkeypatch, ["turn.completed"])
    sent: list[dict[str, Any]] = []
    answers = iter([500, 200])

    async def post(
        url: str, *, content: bytes, headers: dict[str, str], timeout_seconds: float
    ) -> tuple[int, bytes]:
        sent.append({"url": url, "body": content, "headers": headers})
        return next(answers), b""

    monkeypatch.setattr(outbound_webhooks, "safe_post", post)
    monkeypatch.setattr(outbound_webhooks, "_send_queued", AsyncMock())
    assert await outbound_webhooks.emit(None, "org", "turn.completed", {"answer": "x" * 5000}) == 1
    assert await outbound_webhooks.emit(None, "org", "pr.opened", {}) == 0
    delivery_id = outbound_webhooks._memory_deliveries[0]["id"]
    assert await outbound_webhooks.attempt_delivery(None, "org", delivery_id) == "pending"
    delivery = outbound_webhooks._memory_deliveries[0]
    assert delivery["attempts"] == 1 and delivery["next_attempt_at"] > 0
    headers = sent[0]["headers"]
    stamp = int(headers["X-Rocket-Timestamp"])
    assert headers["X-Rocket-Signature"] == sign(created.secret, stamp, sent[0]["body"])
    assert len(json.loads(sent[0]["body"])["data"]["answer"]) == outbound_webhooks.MAX_FIELD_CHARS
    assert await outbound_webhooks.attempt_delivery(None, "org", delivery_id) == "delivered"


async def test_a_report_gives_up_after_the_last_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    await _hook(monkeypatch, ["turn.failed"])

    async def down(url: str, **_: Any) -> tuple[int, bytes]:
        raise OSError("refused")

    monkeypatch.setattr(outbound_webhooks, "safe_post", down)
    monkeypatch.setattr(outbound_webhooks, "_send_queued", AsyncMock())
    await outbound_webhooks.emit(None, "org", "turn.failed", {})
    delivery_id = outbound_webhooks._memory_deliveries[0]["id"]
    statuses = [
        await outbound_webhooks.attempt_delivery(None, "org", delivery_id)
        for _ in range(outbound_webhooks.MAX_ATTEMPTS)
    ]
    assert statuses[-1] == "failed" and statuses[0] == "pending"
    assert outbound_webhooks.next_attempt_delay(1) < outbound_webhooks.next_attempt_delay(3)


# -- Jira and Linear ---------------------------------------------------------------------------------


def test_linear_and_jira_webhooks_must_be_signed(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"type": "Issue", "action": "create", "data": {"identifier": "ENG-3", "title": "Bug",
                       "labels": [{"name": "agent"}]}}).encode()  # fmt: skip
    monkeypatch.delenv("LINEAR_WEBHOOK_SECRET", raising=False)
    with pytest.raises(TrackerSignatureError, match="not configured"):
        linear_event(body, {})
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", "s3")
    with pytest.raises(TrackerSignatureError):
        linear_event(body, {"Linear-Signature": "bad"})
    good = hmac.new(b"s3", body, hashlib.sha256).hexdigest()
    event = linear_event(body, {"Linear-Signature": good})
    assert event is not None and (event.source, event.key, event.labels) == (
        "linear_issue",
        "ENG-3",
        ("agent",),
    )
    monkeypatch.setenv("JIRA_WEBHOOK_SECRET", "j")
    issue = json.dumps({"webhookEvent": "jira:issue_created", "issue": {"key": "ABC-1", "fields": {
        "summary": "Fix", "description": {"type": "doc", "content": [{"type": "paragraph",
        "content": [{"type": "text", "text": "Steps"}]}]}}}}).encode()  # fmt: skip
    signature = "sha256=" + hmac.new(b"j", issue, hashlib.sha256).hexdigest()
    jira = jira_event(issue, {"X-Hub-Signature": signature})
    assert jira is not None and jira.key == "ABC-1" and jira.body == "Steps"


def test_a_ticket_session_is_keyed_by_its_ticket() -> None:
    session = session_id_for(
        _trigger(source="linear_issue"),
        TriggerEvent(source="linear_issue", key="ENG-3", trusted=True),
    )
    assert session == "linear_eng_3"
