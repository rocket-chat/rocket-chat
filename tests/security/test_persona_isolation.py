"""Personas are tenant data: no leaks, no shadowing, no hijacking built-ins."""

from __future__ import annotations

import pytest
from api.main import app
from api.services.personas import RESERVED_IDS
from fastapi.testclient import TestClient

ADMIN_A = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "admin_user", "X-User-Role": "admin"}
MEMBER_A = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "member_user",
    "X-User-Role": "developer",
}
ADMIN_B = {"X-Tenant-Org-Id": "usage_org", "X-Tenant-User-Id": "usage_user", "X-User-Role": "admin"}


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        app.state.db_manager = None  # the process-local development store
        yield c


def _persona(persona_id: str, scope: str = "org", **extra: object) -> dict[str, object]:
    return {
        "id": persona_id,
        "name": persona_id,
        "role_title": "t",
        "system_prompt": "s",
        "scope": scope,
        **extra,
    }


def _ids(client: TestClient, headers: dict[str, str]) -> set[str]:
    return {a["id"] for a in client.get("/v1/agents", headers=headers).json()}


def test_another_organizations_persona_is_invisible(client: TestClient) -> None:
    assert (
        client.post("/v1/agents", json=_persona("secret-agent"), headers=ADMIN_A).status_code == 200
    )

    assert "secret-agent" in _ids(client, ADMIN_A)
    assert "secret-agent" not in _ids(client, ADMIN_B)


def test_built_in_personas_are_always_listed_next_to_custom_ones(client: TestClient) -> None:
    client.post("/v1/agents", json=_persona("mine"), headers=ADMIN_A)

    assert RESERVED_IDS <= _ids(client, ADMIN_A)
    assert RESERVED_IDS <= _ids(client, ADMIN_B)


@pytest.mark.parametrize("reserved", sorted(RESERVED_IDS))
def test_only_administrators_edit_built_in_personas(client: TestClient, reserved: str) -> None:
    assert client.post("/v1/agents", json=_persona(reserved), headers=MEMBER_A).status_code == 403
    assert client.delete(f"/v1/agents/{reserved}", headers=MEMBER_A).status_code == 403


def test_an_edit_of_a_built_in_persona_never_reaches_another_organization(
    client: TestClient,
) -> None:
    edited = client.post(
        "/v1/agents", json=_persona("persona-general", name="Our engineer"), headers=ADMIN_A
    )
    assert edited.status_code == 200

    def named(headers: dict[str, str]) -> str:
        agents = client.get("/v1/agents", headers=headers).json()
        return next(a["name"] for a in agents if a["id"] == "persona-general")

    assert named(ADMIN_A) == "Our engineer" and named(MEMBER_A) == "Our engineer"
    assert named(ADMIN_B) == "General Software Engineer"
    client.delete("/v1/agents/persona-general", headers=ADMIN_A)


def test_a_persona_is_not_resolvable_by_id_from_another_organization(client: TestClient) -> None:
    import asyncio

    from api.turns.routing import resolve_agent_persona

    client.post("/v1/agents", json=_persona("only-in-a"), headers=ADMIN_A)

    mine = asyncio.run(resolve_agent_persona(None, "test_org", "admin_user", "only-in-a"))
    theirs = asyncio.run(resolve_agent_persona(None, "usage_org", "usage_user", "only-in-a"))
    assert mine is not None and theirs is None


def test_members_cannot_edit_org_personas_or_see_each_others_personal_ones(
    client: TestClient,
) -> None:
    client.post("/v1/agents", json=_persona("team"), headers=ADMIN_A)
    edit = client.post("/v1/agents", json=_persona("team", name="hijacked"), headers=MEMBER_A)
    assert edit.status_code == 403

    client.post("/v1/agents", json=_persona("private", scope="user"), headers=MEMBER_A)
    other_member = {**MEMBER_A, "X-Tenant-User-Id": "user_1"}
    assert "private" in _ids(client, MEMBER_A)
    assert "private" not in _ids(client, other_member)
    assert client.delete("/v1/agents/private", headers=other_member).status_code == 403


def test_attachments_survive_a_round_trip(client: TestClient) -> None:
    saved = client.post(
        "/v1/agents",
        json=_persona("with-kb", skill_ids=["sk1"], knowledge_base_ids=["kb1"]),
        headers=ADMIN_A,
    ).json()

    assert saved["skill_ids"] == ["sk1"] and saved["knowledge_base_ids"] == ["kb1"]
