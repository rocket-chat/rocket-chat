"""A signed push webhook brings the sources following that branch forward, and the sweep then
reads them: the skills imported from the repository follow it without anyone asking."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from api.db.session import DatabaseSessionManager
from api.main import app
from api.services.external_sources import external_source_store
from api.services.source_management import SYNC_INTERVAL_SECONDS, synchronise_due
from fastapi.testclient import TestClient
from git_engine.config import GitEngineSettings
from git_engine.engine import GitEngine

from specifications.interfaces.external_sources import ExternalSource, ExternalSourceStoreProtocol
from tests.support.fake_source import FakeRepository, install, skill_md

SECRET = "push-test-secret"


def _signed(event: str, payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-GitHub-Event": event, "X-Hub-Signature-256": signature}


def _push(ref: str = "refs/heads/main", **fields: Any) -> dict[str, Any]:
    return {
        "ref": ref,
        "after": "f" * 40,
        "repository": {"full_name": "Acme/Agent-Skills"},
        "sender": {"login": "dev"},
        **fields,
    }


@contextmanager
def _stores(client: TestClient) -> Iterator[tuple[ExternalSourceStoreProtocol, Any]]:
    """The store the app writes to (PostgreSQL under TEST_DATABASE_URL, else memory), and the
    admin connection the sweep claims sources across organizations with, as in production."""
    db_mgr = getattr(app.state, "db_manager", None)
    admin_url = os.getenv("ADMIN_DATABASE_URL", "")
    if db_mgr is None or getattr(db_mgr, "session_factory", None) is None or not admin_url:
        yield external_source_store(None), None
        return
    admin = DatabaseSessionManager(admin_url)
    try:
        yield external_source_store(db_mgr, admin), admin
    finally:
        client.portal.call(admin.close)


@pytest.fixture
def org(monkeypatch: pytest.MonkeyPatch) -> str:
    org = f"push_{uuid.uuid4().hex[:8]}"
    # An installation that is not mapped falls back to this organization (as deployments do).
    monkeypatch.setenv("ROCKET_WEBHOOK_ORG_ID", org)
    return org


def test_a_push_is_read_and_the_imported_skill_follows(org: str) -> None:
    repo = FakeRepository()
    repo.commit({"logs/SKILL.md": skill_md("logs", "Read logs", "Old steps.")})
    with TestClient(app) as client:
        app.state.git_engine = GitEngine(settings=GitEngineSettings(github_webhook_secret=SECRET))
        install(app, repo)
        admin = {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": "root", "X-User-Role": "admin"}
        source = client.post(
            "/v1/external-sources", json={"repository": "acme/agent-skills"}, headers=admin
        ).json()
        client.post(
            f"/v1/external-sources/{source['id']}/import",
            json={"paths": ["logs"], "scope": "org"},
            headers=admin,
        )
        repo.change("logs/SKILL.md", skill_md("logs", "Read logs", "New steps."))

        body, headers = _signed("push", _push())
        delivered = client.post("/v1/webhooks/github", content=body, headers=headers).json()

        assert delivered == {"status": "scheduled", "sources": [source["id"]]}
        with _stores(client) as (store, admin_db):
            scheduled = client.portal.call(store.get_source, org, source["id"])
            assert scheduled is not None and scheduled.next_sync_at == 0
            now = int(time.time())
            assert client.portal.call(synchronise_due, app.state, admin_db, now) >= 1
            skills = client.get("/v1/skills", headers=admin).json()
            assert next(s for s in skills if s["name"] == "logs")["body"] == "New steps."
            after = client.portal.call(store.get_source, org, source["id"])
            assert after is not None and after.next_sync_at == now + SYNC_INTERVAL_SECONDS


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        ("push", _push(ref="refs/tags/v1")),
        ("push", _push(deleted=True)),
        ("push", _push(ref="refs/heads/develop")),
        ("issues", _push()),
    ],
)
def test_a_tag_a_deleted_branch_another_branch_or_event_schedules_nothing(
    org: str, event: str, payload: dict[str, Any]
) -> None:
    repo = FakeRepository()
    repo.commit({"logs/SKILL.md": skill_md("logs", "Read logs")})
    with TestClient(app) as client:
        app.state.git_engine = GitEngine(settings=GitEngineSettings(github_webhook_secret=SECRET))
        install(app, repo)
        admin = {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": "root", "X-User-Role": "admin"}
        source = client.post(
            "/v1/external-sources", json={"repository": "acme/agent-skills"}, headers=admin
        ).json()

        body, headers = _signed(event, payload)
        delivered = client.post("/v1/webhooks/github", content=body, headers=headers).json()

        assert "sources" not in delivered
        with _stores(client) as (store, _):
            stored = client.portal.call(store.get_source, org, source["id"])
        assert stored is not None and stored.next_sync_at > 0


def test_an_unsigned_push_is_rejected(org: str) -> None:
    with TestClient(app) as client:
        app.state.git_engine = GitEngine(settings=GitEngineSettings(github_webhook_secret=SECRET))
        body = json.dumps(_push()).encode()

        response = client.post(
            "/v1/webhooks/github",
            content=body,
            headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=bad"},
        )

        assert response.status_code == 401


async def test_a_source_that_fails_does_not_stop_the_sweep(org: str) -> None:
    repo = FakeRepository()
    repo.commit({"logs/SKILL.md": skill_md("logs", "Read logs")})
    install(app, repo)
    store = external_source_store(None)
    for name in ("a", "b"):
        await store.save_source(
            ExternalSource(
                id=f"src_{org}_{name}",
                org_id=org,
                name=name,
                repository="acme/agent-skills",
                ref="main" if name == "b" else "gone",
            )
        )

    assert await synchronise_due(app.state, None) >= 2
    failed = await store.get_source(org, f"src_{org}_a")
    read = await store.get_source(org, f"src_{org}_b")
    assert failed is not None and failed.status == "error"
    assert read is not None and read.status == "ok"
