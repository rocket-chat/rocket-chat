"""The sandbox catalogue: seeding, administration, session choice and capabilities."""

from __future__ import annotations

from typing import Any

import pytest
from api.services.sandbox_catalogue import SandboxCatalogue
from api.services.settings import save_org_config
from fastapi.testclient import TestClient

ADMIN = {"X-Tenant-Org-Id": "cat_org", "X-Tenant-User-Id": "cat_admin", "X-User-Role": "admin"}
MEMBER = {**ADMIN, "X-Tenant-User-Id": "cat_member", "X-User-Role": "developer"}


@pytest.fixture(autouse=True)
def _fresh_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SandboxCatalogue, "_MEMORY", {})


def _names(client: TestClient, headers: dict[str, str] = MEMBER) -> list[str]:
    return [s["name"] for s in client.get("/v1/sandboxes", headers=headers).json()["sandboxes"]]


def test_an_organization_starts_with_the_platform_default_in_its_catalogue(
    client: TestClient,
) -> None:
    body = client.get("/v1/sandboxes", headers=MEMBER).json()
    default = body["sandboxes"][0]
    assert default["name"] == "Devbox" and default["is_default"] and default["capabilities"] == []
    shipped = {e["name"]: e for e in body["sandboxes"][1:]}
    assert set(shipped) == {"Browser", "Polyglot"}
    assert (
        shipped["Browser"]["capabilities"] == ["browser"] and not shipped["Polyglot"]["is_default"]
    )
    assert "browser" in body["capabilities"], "the page can explain what each capability needs"


def test_the_catalogue_is_seeded_from_what_the_organization_already_configured(
    client: TestClient,
) -> None:
    import asyncio

    asyncio.run(
        save_org_config(
            None,
            "legacy_org",
            "sandboxes",
            {
                "default_image": "registry.example/custom:1",
                "allowed_images": ["registry.example/other:2", "registry.example/*"],
            },
            [],
        )
    )
    headers = {**MEMBER, "X-Tenant-Org-Id": "legacy_org"}
    entries = client.get("/v1/sandboxes", headers=headers).json()["sandboxes"]
    entries = [e for e in entries if e["name"] not in ("Browser", "Polyglot")]
    assert [(e["image"], e["is_default"]) for e in entries] == [
        ("registry.example/custom:1", True),
        ("registry.example/other:2", False),
    ], "patterns cannot become entries; they keep working as allowed patterns"


def test_only_administrators_change_the_catalogue(client: TestClient) -> None:
    body = {"name": "Headless", "image": "registry.example/box:1", "capabilities": ["browser"]}
    assert client.post("/v1/admin/sandboxes", json=body, headers=MEMBER).status_code == 403
    created = client.post("/v1/admin/sandboxes", json=body, headers=ADMIN)
    assert created.status_code == 200 and created.json()["capabilities"] == ["browser"]
    assert "Headless" in _names(client)


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "image": "has space:1"},
        {"name": "x", "image": "ok:1", "capabilities": ["teleport"]},
        {"name": "Devbox", "image": "ok:1"},
    ],
)
def test_a_bad_entry_is_refused_with_the_reason(client: TestClient, body: dict[str, Any]) -> None:
    client.get("/v1/sandboxes", headers=ADMIN)
    refused = client.post("/v1/admin/sandboxes", json=body, headers=ADMIN)
    assert refused.status_code == 422 and refused.json()["detail"]


def test_the_default_and_the_last_entry_cannot_be_removed(client: TestClient) -> None:
    default = client.get("/v1/sandboxes", headers=ADMIN).json()["sandboxes"][0]
    assert client.delete(f"/v1/admin/sandboxes/{default['id']}", headers=ADMIN).status_code == 409

    other = client.post(
        "/v1/admin/sandboxes", json={"name": "B", "image": "b:1"}, headers=ADMIN
    ).json()
    assert client.delete(f"/v1/admin/sandboxes/{default['id']}", headers=ADMIN).status_code == 409
    switched = client.patch(
        f"/v1/admin/sandboxes/{other['id']}", json={"is_default": True}, headers=ADMIN
    )
    assert switched.status_code == 200 and switched.json()["is_default"]
    assert client.delete(f"/v1/admin/sandboxes/{default['id']}", headers=ADMIN).status_code == 200
    assert client.get("/v1/sandboxes", headers=ADMIN).json()["sandboxes"][0]["id"] == other["id"]


def test_a_session_picks_a_sandbox_by_id_and_unknown_choices_are_refused(
    client: TestClient,
) -> None:
    box = client.post(
        "/v1/admin/sandboxes",
        json={
            "name": "Headless",
            "image": "registry.example/browser:1",
            "capabilities": ["browser"],
        },
        headers=ADMIN,
    ).json()
    created = client.post("/v1/sessions", json={"sandbox_id": box["id"]}, headers=MEMBER)
    assert created.status_code == 200
    body = created.json()
    assert body["container_image"] == "registry.example/browser:1"
    assert body["metadata"]["sandbox_id"] == box["id"]

    assert (
        client.post("/v1/sessions", json={"sandbox_id": "sbx_nope"}, headers=MEMBER).status_code
        == 422
    )
    other = client.post("/v1/sessions", json={"container_image": "evil/image:1"}, headers=MEMBER)
    assert other.status_code == 422 and "catalogue" in other.json()["detail"]
    named = client.post(
        "/v1/sessions", json={"container_image": "registry.example/browser:1"}, headers=MEMBER
    )
    assert named.status_code == 200 and named.json()["metadata"]["sandbox_id"] == box["id"]


async def test_resolution_prefers_the_id_then_the_image_then_the_default() -> None:
    catalogue = SandboxCatalogue(None)
    first = await catalogue.create("r_org", name="A", image="a:1", is_default=True)
    second = await catalogue.create("r_org", name="B", image="b:1", capabilities=["browser"])
    assert (await catalogue.resolve("r_org", sandbox_id=second.id)) == second
    assert (await catalogue.resolve("r_org", image="b:1")) == second
    assert (await catalogue.resolve("r_org")) == first
    assert await catalogue.resolve("r_org", image="unknown:1") is None
    assert await catalogue.image_is_offered("r_org", "z/anything:1", ["z/*"])
    assert not await catalogue.image_is_offered("r_org", "y/anything:1", ["z/*"])
