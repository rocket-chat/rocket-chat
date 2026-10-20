"""The sandbox catalogue on PostgreSQL: concurrent first use seeds once, tenants stay apart."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from api.db.session import current_tenant_org_id
from api.main import app
from api.services.sandbox_catalogue import SandboxCatalogue
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs PostgreSQL")

HEADERS = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "cat_db_admin",
    "X-User-Role": "admin",
}


def test_two_requests_arriving_together_seed_the_catalogue_once() -> None:
    with TestClient(app) as client:
        manager = app.state.db_manager

        async def first_use_twice() -> int:
            current_tenant_org_id.set("test_org")
            catalogue = SandboxCatalogue(manager)
            await asyncio.gather(catalogue.entries("test_org"), catalogue.entries("test_org"))
            return len(await catalogue.entries("test_org"))

        before = client.get("/v1/sandboxes", headers=HEADERS).json()["sandboxes"]
        count = client.portal.call(first_use_twice)
    assert count == len(before) >= 1
    assert sum(1 for e in before if e["is_default"]) == 1


def test_an_entry_is_invisible_to_another_organization() -> None:
    name = f"private-{uuid.uuid4().hex[:6]}"
    other = {**HEADERS, "X-Tenant-Org-Id": "usage_org", "X-Tenant-User-Id": "other_admin"}
    with TestClient(app) as client:
        created = client.post(
            "/v1/admin/sandboxes",
            json={"name": name, "image": "registry.example/private:1"},
            headers=HEADERS,
        )
        assert created.status_code == 200, created.text
        mine = [s["name"] for s in client.get("/v1/sandboxes", headers=HEADERS).json()["sandboxes"]]
        theirs = [s["name"] for s in client.get("/v1/sandboxes", headers=other).json()["sandboxes"]]
        client.delete(f"/v1/admin/sandboxes/{created.json()['id']}", headers=HEADERS)
    assert name in mine and name not in theirs


def test_variables_round_trip_with_the_secret_sealed() -> None:
    name = f"env-{uuid.uuid4().hex[:6]}"
    body = {
        "name": name,
        "image": "registry.example/env:1",
        "env": [
            {"name": "GOPRIVATE", "value": "github.com/acme/*"},
            {"name": "NPM_TOKEN", "value": "npm_s3cret", "secret": True},
        ],
    }
    with TestClient(app) as client:
        created = client.post("/v1/admin/sandboxes", json=body, headers=HEADERS)
        assert created.status_code == 200, created.text
        box_id = created.json()["id"]

        async def stored() -> list[dict[str, object]]:
            current_tenant_org_id.set("test_org")
            entries = await SandboxCatalogue(app.state.db_manager).entries("test_org")
            return next(e for e in entries if e.id == box_id).env

        env = client.portal.call(stored)
        client.delete(f"/v1/admin/sandboxes/{box_id}", headers=HEADERS)
    assert env[0] == {"name": "GOPRIVATE", "value": "github.com/acme/*", "secret": False}
    assert env[1]["secret"] is True and env[1]["value"] != "npm_s3cret"
