"""An agent's own step limit may go up to the organization's limit and no higher."""

from __future__ import annotations

import pytest
from api.services.settings import reset_memory_settings, save_org_config
from fastapi.testclient import TestClient

from specifications.interfaces.agent import NO_STEP_LIMIT, combine_step_limits

ORG = "agent_limit_org"
ADMIN = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "admin_user", "X-User-Role": "admin"}
MEMBER = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "member_user", "X-User-Role": "developer"}


@pytest.fixture(autouse=True)
async def _org_limit() -> None:
    await save_org_config(None, ORG, "general", {"max_turns": 40}, [])
    yield  # type: ignore[misc]
    reset_memory_settings()


def _persona(max_turns: int | None, scope: str = "user") -> dict[str, object]:
    body: dict[str, object] = {
        "id": "persona-limit",
        "name": "Researcher",
        "role_title": "Analyst",
        "system_prompt": "Research things.",
        "scope": scope,
    }
    if max_turns is not None:
        body["max_turns"] = max_turns
    return body


def test_the_limits_endpoint_reports_the_organizations_ceiling(client: TestClient) -> None:
    response = client.get("/v1/agents/limits", headers=MEMBER)

    assert response.status_code == 200 and response.json() == {"max_turns": 40}


def test_an_agent_may_set_any_limit_up_to_the_organizations(client: TestClient) -> None:
    for value in (1, 25, 40):
        saved = client.post("/v1/agents", json=_persona(value), headers=MEMBER)
        assert saved.status_code == 200, saved.text
        assert saved.json()["max_turns"] == value


def test_a_limit_above_the_organizations_is_refused_with_the_ceiling(client: TestClient) -> None:
    refused = client.post("/v1/agents", json=_persona(41), headers=MEMBER)

    assert refused.status_code == 422
    assert "limit of 40" in refused.text


def test_an_empty_limit_inherits_the_organizations(client: TestClient) -> None:
    saved = client.post("/v1/agents", json=_persona(None), headers=ADMIN)

    assert saved.status_code == 200 and saved.json()["max_turns"] is None


def test_no_limit_is_refused_under_a_finite_organization_limit(client: TestClient) -> None:
    refused = client.post("/v1/agents", json=_persona(0), headers=MEMBER)

    assert refused.status_code == 422 and "limit of 40" in refused.text


async def test_an_organization_without_a_limit_accepts_agents_without_one(
    client: TestClient,
) -> None:
    await save_org_config(None, ORG, "general", {"max_turns": 0}, [])

    saved = client.post("/v1/agents", json=_persona(0), headers=MEMBER)

    assert saved.status_code == 200, saved.text
    assert saved.json()["max_turns"] == 0


def test_step_limits_combine_with_no_limit_as_the_largest_value() -> None:
    assert combine_step_limits(None, 40) == 40
    assert combine_step_limits(25, 40) == 25
    assert combine_step_limits(NO_STEP_LIMIT, 40) == 40
    assert combine_step_limits(None, NO_STEP_LIMIT) == NO_STEP_LIMIT
    assert combine_step_limits(300, NO_STEP_LIMIT) == 300
