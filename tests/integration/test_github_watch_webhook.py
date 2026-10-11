"""A signed GitHub webhook reaches the session that follows the pull request, and no other."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from api.events import AsyncIOEventBus
from api.main import app
from api.services import github_events
from api.services.github_watches import GithubWatchStore
from api.services.metrics import SessionWriteLocks
from api.store import InMemorySessionStore
from git_engine.config import GitEngineSettings
from git_engine.engine import GitEngine
from httpx import ASGITransport, AsyncClient

from specifications.interfaces.session import SessionRecord

SECRET = "watch-test-secret"
ORG = "default_org"


def _signed(event: str, payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-GitHub-Event": event, "X-Hub-Signature-256": signature}


def _check_suite(pr_number: int, conclusion: str = "failure") -> dict[str, Any]:
    return {
        "action": "completed",
        "repository": {"full_name": "acme/api"},
        "sender": {"login": "github-actions"},
        "check_suite": {
            "conclusion": conclusion,
            "head_branch": "agent/fix",
            "head_sha": "abcdef1234",
            "app": {"name": "GitHub Actions"},
            "pull_requests": [{"number": pr_number}],
        },
    }


@pytest.fixture
async def wired(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    store = InMemorySessionStore()
    await store.create_session(
        SessionRecord(session_id="s1", tenant_org_id=ORG, tenant_user_id="u", title="t")
    )
    app.state.session_store = store
    app.state.event_bus = AsyncIOEventBus()
    app.state.session_write_locks = SessionWriteLocks(8)
    app.state.git_engine = GitEngine(settings=GitEngineSettings(github_webhook_secret=SECRET))
    monkeypatch.delenv("ROCKET_WEBHOOK_ORG_ID", raising=False)
    started: list[tuple[str, str]] = []

    async def start(_app: Any, _org: str, session_id: str, prompt: str, **_: Any) -> bool:
        started.append((session_id, prompt))
        return True

    monkeypatch.setattr(github_events, "start_turn_on_session", start)
    return started


async def _post(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    body, headers = _signed(event, payload)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


async def test_ci_failure_on_a_followed_pull_request_wakes_that_session(
    wired: list[tuple[str, str]],
) -> None:
    await GithubWatchStore(None).watch(ORG, "s1", "acme/api", pr_number=7)

    result = await _post("check_suite", _check_suite(7))

    assert result == {"status": "delivered", "sessions": ["s1"]}
    assert [session_id for session_id, _ in wired] == ["s1"]
    assert "failure" in wired[0][1]


async def test_an_unfollowed_pull_request_reaches_nobody(wired: list[tuple[str, str]]) -> None:
    await GithubWatchStore(None).watch(ORG, "s1", "acme/api", pr_number=7)

    result = await _post("check_suite", _check_suite(8))

    assert result["status"] == "ignored" and wired == []


async def test_an_unsigned_event_is_rejected_and_delivers_nothing(
    wired: list[tuple[str, str]],
) -> None:
    await GithubWatchStore(None).watch(ORG, "s1", "acme/api", pr_number=7)
    body = json.dumps(_check_suite(7)).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/webhooks/github",
            content=body,
            headers={"X-GitHub-Event": "check_suite", "X-Hub-Signature-256": "sha256=bad"},
        )

    assert response.status_code == 401 and wired == []
