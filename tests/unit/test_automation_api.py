"""The automation routes: administrators manage triggers and outbound webhooks; members run
``api`` triggers and nothing else."""

from __future__ import annotations

from typing import Any

import pytest
from api.services import outbound_webhooks, triggers
from fastapi.testclient import TestClient

ADMIN = {"X-Tenant-Org-Id": "auto_org", "X-Tenant-User-Id": "admin_user", "X-User-Role": "admin"}
MEMBER = {**ADMIN, "X-Tenant-User-Id": "member_user", "X-User-Role": "member"}
TRIGGER: dict[str, Any] = {
    "name": "Review backend",
    "source": "github_pr",
    "filter": {"repositories": ["acme/*"], "labels": [], "branches": []},
    "mode": "review",
}


@pytest.fixture(autouse=True)
def _clean() -> Any:
    triggers._memory.clear()
    triggers._claimed.clear()
    outbound_webhooks.reset_memory()
    yield


def test_an_administrator_manages_triggers_and_a_member_cannot(client: TestClient) -> None:
    assert client.post("/v1/admin/triggers", json=TRIGGER, headers=MEMBER).status_code == 403
    assert client.get("/v1/admin/triggers", headers=MEMBER).status_code == 403
    created = client.post("/v1/admin/triggers", json=TRIGGER, headers=ADMIN)
    assert created.status_code == 201, created.text
    trigger_id = created.json()["id"]
    listed = client.get("/v1/admin/triggers", headers=ADMIN).json()["triggers"]
    assert [t["id"] for t in listed] == [trigger_id]
    off = client.put(
        f"/v1/admin/triggers/{trigger_id}", json={**TRIGGER, "enabled": False}, headers=ADMIN
    )
    assert off.json()["enabled"] is False
    assert client.delete(f"/v1/admin/triggers/{trigger_id}", headers=ADMIN).status_code == 204
    assert client.delete(f"/v1/admin/triggers/{trigger_id}", headers=ADMIN).status_code == 404


def test_an_invalid_trigger_is_refused_with_its_reason(client: TestClient) -> None:
    bad = client.post(
        "/v1/admin/triggers", json={**TRIGGER, "source": "github_issue_label"}, headers=ADMIN
    )
    assert bad.status_code == 422 and "pull request" in bad.json()["detail"]


def test_a_member_runs_an_api_trigger_once_per_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[str] = []
    monkeypatch.setattr(
        triggers, "_spawn_background_turn", lambda **kw: started.append(kw["session_id"])
    )
    api_trigger = {**TRIGGER, "source": "api", "mode": "work", "filter": {}}
    trigger_id = client.post("/v1/admin/triggers", json=api_trigger, headers=ADMIN).json()["id"]
    body = {"prompt": "Bump the version", "idempotency_key": "job-42"}
    first = client.post(f"/v1/triggers/{trigger_id}/run", json=body, headers=MEMBER)
    assert first.status_code == 200 and first.json()["status"] == "created"
    assert (
        client.post(f"/v1/triggers/{trigger_id}/run", json=body, headers=MEMBER).json()["status"]
        == "duplicate"
    )
    assert len(started) == 1
    assert client.post("/v1/triggers/nope/run", json=body, headers=MEMBER).status_code == 404


def test_webhooks_are_for_administrators_and_their_secret_is_shown_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def public(url: str) -> Any:
        return type("T", (), {"host": "hooks.example.com", "address": "93.184.216.34"})()

    monkeypatch.setattr(outbound_webhooks, "resolve_public_target", public)
    body = {"url": "https://hooks.example.com/r", "events": ["turn.completed"]}
    assert client.post("/v1/admin/webhooks", json=body, headers=MEMBER).status_code == 403
    created = client.post("/v1/admin/webhooks", json=body, headers=ADMIN)
    assert created.status_code == 201 and created.json()["secret"].startswith("whsec_")
    listed = client.get("/v1/admin/webhooks", headers=ADMIN).json()["webhooks"]
    assert "secret" not in listed[0] and listed[0]["secret_fingerprint"]
    assert (
        client.post(
            "/v1/admin/webhooks", json={**body, "url": "http://x.example"}, headers=ADMIN
        ).status_code
        == 422
    )
