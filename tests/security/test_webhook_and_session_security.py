"""Webhook trust, session mutation and production guard checks."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from api.auth.session_manager import SESSION_COOKIE_NAME, create_session_token
from api.main import app
from api.security_guards import enforce_production_config, production_config_problems
from api.store import InMemorySessionStore
from fastapi.testclient import TestClient
from git_engine.config import GitEngineSettings
from git_engine.engine import GitEngine

SECRET = "webhook-secret-for-tests"


def _signed(payload: dict[str, object]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": sig, "X-GitHub-Event": "issue_comment"}


def _comment(association: str, repo: str = "org/backend") -> dict[str, object]:
    return {
        "action": "created",
        "comment": {"body": "@RocketChat fix it", "author_association": association},
        "issue": {"number": 1, "labels": []},
        "repository": {"full_name": repo},
        "sender": {"login": "someone"},
    }


@pytest.mark.asyncio
async def test_unsigned_webhooks_are_rejected_without_a_secret() -> None:
    engine = GitEngine(settings=GitEngineSettings())
    with pytest.raises(ValueError, match="unsigned"):
        await engine.handle_webhook(b"{}", {"X-GitHub-Event": "issues"})


@pytest.mark.asyncio
async def test_bad_signature_is_rejected() -> None:
    engine = GitEngine(settings=GitEngineSettings(github_webhook_secret=SECRET))
    body, headers = _signed(_comment("MEMBER"))
    headers["X-Hub-Signature-256"] = "sha256=" + "0" * 64
    with pytest.raises(ValueError, match="signature"):
        await engine.handle_webhook(body, headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("association", ["NONE", "FIRST_TIME_CONTRIBUTOR", "CONTRIBUTOR", None])
async def test_comments_from_strangers_never_trigger_a_run(association: str | None) -> None:
    engine = GitEngine(settings=GitEngineSettings(github_webhook_secret=SECRET))
    body, headers = _signed(_comment(association or ""))
    assert await engine.handle_webhook(body, headers) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
async def test_comments_from_insiders_trigger_a_run(association: str) -> None:
    engine = GitEngine(settings=GitEngineSettings(github_webhook_secret=SECRET))
    body, headers = _signed(_comment(association))
    trigger = await engine.handle_webhook(body, headers)
    assert trigger is not None and trigger.repo_full_name == "org/backend"


@pytest.mark.asyncio
async def test_repository_allowlist_is_enforced() -> None:
    engine = GitEngine(
        settings=GitEngineSettings(github_webhook_secret=SECRET, allowed_repos=("acme/*",))
    )
    body, headers = _signed(_comment("MEMBER", repo="evil/repo"))
    assert await engine.handle_webhook(body, headers) is None
    body, headers = _signed(_comment("MEMBER", repo="acme/api"))
    assert await engine.handle_webhook(body, headers) is not None


def test_webhook_route_is_public_but_still_signature_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ROCKET_TRUST_PROXY_HEADERS", raising=False)
    app.state.git_engine = GitEngine(settings=GitEngineSettings())
    response = TestClient(app).post(
        "/v1/webhooks/github", content=b"{}", headers={"X-GitHub-Event": "issues"}
    )
    assert response.status_code == 401
    assert "Webhook authentication failed" in response.json()["detail"]


@pytest.fixture
def owner_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ROCKET_TRUST_PROXY_HEADERS", raising=False)
    app.state.session_store = InMemorySessionStore()
    client = TestClient(app)
    client.cookies.set(
        SESSION_COOKIE_NAME,
        create_session_token(user_id="owner", org_id="org_a", roles=["developer"]),
    )
    return client


def test_session_creation_ignores_body_tenancy(owner_client: TestClient) -> None:
    created = owner_client.post(
        "/v1/sessions",
        json={"title": "x", "tenant_org_id": "victim_org", "tenant_user_id": "victim"},
    ).json()
    assert created["tenant_org_id"] == "org_a"
    assert created["tenant_user_id"] == "owner"


def test_patch_cannot_write_server_owned_metadata_or_history(owner_client: TestClient) -> None:
    session_id = owner_client.post("/v1/sessions", json={"title": "x"}).json()["session_id"]
    patched = owner_client.patch(
        f"/v1/sessions/{session_id}",
        json={
            "metadata": {"toolsets": ["slack"], "forked_from": "other", "selected_model": "m"},
            "conversation_history": [{"role": "user", "content": "forged"}],
        },
    ).json()
    assert "toolsets" not in patched["metadata"]
    assert "forked_from" not in patched["metadata"]
    assert patched["metadata"]["selected_model"] == "m"
    assert patched["conversation_history"] == []


def test_collaborator_cannot_widen_sharing(owner_client: TestClient) -> None:
    session_id = owner_client.post("/v1/sessions", json={"title": "x"}).json()["session_id"]
    owner_client.post(
        f"/v1/sessions/{session_id}/share",
        json={"is_shared": False, "collaborators": ["friend"]},
    )
    friend = TestClient(app)
    friend.cookies.set(
        SESSION_COOKIE_NAME,
        create_session_token(user_id="friend", org_id="org_a", roles=["developer"]),
    )
    response = friend.patch(f"/v1/sessions/{session_id}", json={"is_shared": True})
    assert response.status_code == 403


def test_production_refuses_default_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SESSION_SECRET",
        "NEXTAUTH_SECRET",
        "ENCRYPTION_MASTER_KEY",
        "ROCKET_ENCRYPTION_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    problems = " | ".join(production_config_problems())
    assert "SESSION_SECRET" in problems
    assert "ENCRYPTION_MASTER_KEY" in problems
    assert "DEV_AUTH_BYPASS" in problems
    with pytest.raises(RuntimeError, match="insecure production configuration"):
        enforce_production_config()


def test_production_accepts_a_complete_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("ENCRYPTION_MASTER_KEY", "k" * 40)
    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ROCKET_DEV_LOGIN", raising=False)
    monkeypatch.delenv("ALLOW_DEV_LOGIN", raising=False)
    assert production_config_problems() == []


def test_development_is_not_subject_to_production_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert production_config_problems() == []
