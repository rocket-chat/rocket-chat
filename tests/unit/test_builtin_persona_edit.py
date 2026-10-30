"""An administrator can edit a built-in agent for the organization, and reset it."""

from __future__ import annotations

from typing import Any

import pytest
from api.main import app
from api.services.personas import get_persona, reset_memory_personas
from fastapi.testclient import TestClient

ORG = "builtin_edit_org"
ADMIN = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "admin_user", "X-User-Role": "admin"}
MEMBER = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "member_user", "X-User-Role": "developer"}


@pytest.fixture
def client() -> Any:
    with TestClient(app) as c:
        app.state.db_manager = None
        reset_memory_personas()
        yield c
        reset_memory_personas()


def _general(client: TestClient, headers: dict[str, str] = MEMBER) -> dict[str, Any]:
    agents = client.get("/v1/agents", headers=headers).json()
    return next(a for a in agents if a["id"] == "persona-general")


def _edit(**changes: Any) -> dict[str, Any]:
    return {
        "id": "persona-general",
        "name": "General Software Engineer",
        "role_title": "Senior Autonomous Engineer",
        "system_prompt": "Always answer in French.",
        "whitelisted_tools": ["*"],
        **changes,
    }


def test_the_edit_applies_to_every_member_and_is_marked_customized(client: TestClient) -> None:
    assert _general(client)["customized"] is False

    saved = client.post("/v1/agents", json=_edit(max_turns=20), headers=ADMIN)

    assert saved.status_code == 200, saved.text
    seen = _general(client)
    assert seen["system_prompt"] == "Always answer in French."
    assert seen["max_turns"] == 20 and seen["customized"] is True and seen["built_in"] is True
    assert seen["scope"] == "org"


def test_a_second_save_updates_the_same_edit_and_reset_restores_the_shipped_agent(
    client: TestClient,
) -> None:
    client.post("/v1/agents", json=_edit(), headers=ADMIN)
    client.post("/v1/agents", json=_edit(system_prompt="Be terse."), headers=ADMIN)
    assert _general(client)["system_prompt"] == "Be terse."

    assert client.delete("/v1/agents/persona-general", headers=ADMIN).status_code == 200

    restored = _general(client)
    assert (
        restored["customized"] is False
        and "versatile senior software engineer" in restored["system_prompt"]
    )


def test_a_member_cannot_edit_or_reset_a_built_in_agent(client: TestClient) -> None:
    assert client.post("/v1/agents", json=_edit(), headers=MEMBER).status_code == 403
    client.post("/v1/agents", json=_edit(), headers=ADMIN)
    assert client.delete("/v1/agents/persona-general", headers=MEMBER).status_code == 403
    assert _general(client)["customized"] is True


def test_the_step_limit_ceiling_applies_to_built_in_edits_too(client: TestClient) -> None:
    refused = client.post("/v1/agents", json=_edit(max_turns=500), headers=ADMIN)

    assert refused.status_code == 422


async def test_turns_resolve_the_edited_built_in_agent(client: TestClient) -> None:
    client.post("/v1/agents", json=_edit(system_prompt="Use the edit."), headers=ADMIN)

    resolved = await get_persona(None, ORG, "member_user", "persona-general")

    assert resolved is not None and resolved.system_prompt == "Use the edit."
    other_org = await get_persona(None, "someone_else", "u", "persona-general")
    assert other_org is not None and other_org.system_prompt != "Use the edit."


def test_an_administrator_can_disable_a_builtin_agent_and_keep_it_disabled(
    client: TestClient,
) -> None:
    # Disable via POST
    res = client.post("/v1/agents", json=_edit(enabled=False), headers=ADMIN)
    assert res.status_code == 200
    assert all(
        a["id"] != "persona-general" for a in client.get("/v1/agents", headers=MEMBER).json()
    ), "members no longer see a disabled agent"
    seen = _general(client, ADMIN)
    assert seen["enabled"] is False and seen["customized"] is True

    # Reset restores factory defaults
    client.delete("/v1/agents/persona-general", headers=ADMIN)
    assert _general(client)["enabled"] is True

    # Disable via DELETE ?disable=true
    res2 = client.delete("/v1/agents/persona-general?disable=true", headers=ADMIN)
    assert res2.status_code == 200
    seen2 = _general(client, ADMIN)
    assert seen2["enabled"] is False and seen2["customized"] is True

    # Reset again restores factory defaults
    client.delete("/v1/agents/persona-general", headers=ADMIN)
    restored = _general(client)
    assert restored["enabled"] is True and restored["customized"] is False


def test_an_administrator_can_disable_a_builtin_subagent(
    client: TestClient,
) -> None:
    # Disable via DELETE ?disable=true
    res = client.delete("/v1/subagents/researcher?disable=true", headers=ADMIN)
    assert res.status_code == 200
    subagents = {s["id"]: s for s in client.get("/v1/subagents", headers=ADMIN).json()}
    assert subagents["researcher"]["enabled"] is False

    # Other org is unaffected
    other_subagents = {
        s["id"]: s
        for s in client.get(
            "/v1/subagents",
            headers={
                "X-Tenant-Org-Id": "other_org",
                "X-Tenant-User-Id": "u",
                "X-User-Role": "admin",
            },
        ).json()
    }
    assert other_subagents["researcher"]["enabled"] is True

    # Reset restores it
    res_reset = client.delete("/v1/subagents/researcher", headers=ADMIN)
    assert res_reset.status_code == 200
    restored = {s["id"]: s for s in client.get("/v1/subagents", headers=ADMIN).json()}
    assert restored["researcher"]["enabled"] is True


async def test_a_disabled_agent_persona_is_rejected_on_turn_planning(client: TestClient) -> None:
    from api.services.agent_access import AgentAccessDenied
    from api.turns.shared import _turn_service

    client.post("/v1/agents", json=_edit(enabled=False), headers=ADMIN)
    with pytest.raises(AgentAccessDenied, match="has been disabled"):
        await _turn_service.plan_turn(
            db_mgr=None,
            org_id=ORG,
            user_id="member_user",
            agent_id="persona-general",
        )
