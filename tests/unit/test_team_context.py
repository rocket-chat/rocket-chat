"""A team's agent runs in its team's context: the team's keys pay before the organization's and
the team's spend limits count the turn, whoever started it."""

from __future__ import annotations

from typing import Any

import pytest
from api.models import AgentPersonaModel
from api.services.budgets import BudgetService
from api.services.model_credentials import CredentialResolver, ModelCredentialStore, NewCredential
from api.services.personas import save_persona
from api.services.settings import reset_memory_settings, save_org_config
from api.services.spend_limits import SpendLimit, applicable
from api.services.teams import create_team, set_team_member
from api.services.usage import UsageLedger
from fastapi.testclient import TestClient

from specifications.interfaces.llm import (
    BudgetExceededError,
    CredentialBinding,
    CredentialRefusedError,
    ModelScope,
)
from tests.turn_harness import run_turn

ORG = "team_ctx"
MODEL = "anthropic/claude-sonnet-4-5"


@pytest.fixture(autouse=True)
def _clean() -> None:
    reset_memory_settings()


async def _keys() -> tuple[ModelCredentialStore, dict[str, str]]:
    store = ModelCredentialStore(None)
    ids = {
        "org": (
            await store.create(ORG, NewCredential("org", None, "anthropic", "sk-org-00001", "Org"))
        ).id,
        "platform": (
            await store.create(
                ORG,
                NewCredential(
                    "team", None, "anthropic", "sk-team-0001", "Platform", team_id="platform"
                ),
            )
        ).id,
        "alice": (
            await store.create(
                ORG, NewCredential("member", "alice", "anthropic", "sk-alice-001", "Alice")
            )
        ).id,
    }
    return store, ids


def _resolver(store: ModelCredentialStore) -> CredentialResolver:
    async def may_use(org_id: str, user_id: str) -> bool:
        return True

    return CredentialResolver(None, store, member_may_use_keys=may_use)


async def test_a_team_key_pays_before_the_organizations_in_the_teams_context() -> None:
    store, ids = await _keys()
    await save_org_config(None, ORG, "models", {"member_keys": "off"}, [])
    resolver = _resolver(store)
    in_team = await resolver.resolve(ModelScope(ORG, "bob", team_id="platform"), MODEL)
    outside = await resolver.resolve(ModelScope(ORG, "bob"), MODEL)
    other_team = await resolver.resolve(ModelScope(ORG, "bob", team_id="payments"), MODEL)
    assert in_team is not None and (in_team.key_source, in_team.credential_id) == (
        "team",
        ids["platform"],
    )
    assert outside is not None and outside.credential_id == ids["org"]
    assert other_team is not None and other_team.credential_id == ids["org"]


async def test_a_members_preferred_key_still_comes_first() -> None:
    store, ids = await _keys()
    await save_org_config(None, ORG, "models", {"member_keys": "preferred"}, [])
    chosen = await _resolver(store).resolve(ModelScope(ORG, "alice", team_id="platform"), MODEL)
    assert chosen is not None and chosen.credential_id == ids["alice"]
    await save_org_config(None, ORG, "models", {"member_keys": "fallback"}, [])
    chosen = await _resolver(store).resolve(ModelScope(ORG, "alice", team_id="platform"), MODEL)
    assert chosen is not None and chosen.credential_id == ids["platform"]


async def test_a_team_key_bound_to_an_agent_that_left_the_team_is_withdrawn() -> None:
    store, ids = await _keys()
    binding = (CredentialBinding("anthropic", ids["platform"]),)
    bound = await _resolver(store).resolve(
        ModelScope(ORG, "bob", team_id="platform", credential_bindings=binding), MODEL
    )
    assert bound is not None and bound.key_source == "agent"
    with pytest.raises(CredentialRefusedError):
        await _resolver(store).resolve(
            ModelScope(ORG, "bob", team_id=None, credential_bindings=binding), MODEL
        )


def _limit(**fields: Any) -> SpendLimit:
    base: dict[str, Any] = {
        "id": "lim",
        "org_id": ORG,
        "subject_kind": "team",
        "subject_id": "platform",
        "member_id": None,
        "period": "month",
        "amount_usd": 1.0,
        "mode": "block",
        "warn_at_percent": 80,
        "is_ceiling": False,
        "created_by": "root",
        "created_at": 0,
        "updated_at": 0,
    }
    return SpendLimit(**{**base, **fields})


def test_a_team_limit_counts_the_teams_agents_only() -> None:
    limit = _limit()
    assert applicable([limit], ModelScope(ORG, "bob", team_id="platform"), "team")
    assert applicable([limit], ModelScope(ORG, "bob", team_id="platform"), "org")
    assert not applicable([limit], ModelScope(ORG, "bob"), "org")
    assert not applicable([limit], ModelScope(ORG, "bob", team_id="payments"), "org")
    # A member's own key is their money, team context or not.
    assert not applicable([limit], ModelScope(ORG, "bob", team_id="platform"), "user")


async def test_a_team_limit_blocks_the_call_that_would_cross_it() -> None:
    service = BudgetService(None, UsageLedger())
    await service.store.create(_limit())
    scope = ModelScope(ORG, "bob", team_id="platform")
    first = await service.reserve(scope, estimate_usd=0.7, key_source="team")
    assert first is not None
    await service.settle(first, actual_usd=0.7)
    with pytest.raises(BudgetExceededError):
        await service.reserve(scope, estimate_usd=0.7, key_source="team")
    # Outside the team's context the team's limit does not apply: nothing is reserved.
    assert await service.reserve(ModelScope(ORG, "bob"), estimate_usd=0.7, key_source="org") is None


async def test_a_turn_of_a_team_agent_carries_the_team_whoever_starts_it() -> None:
    team = await create_team(None, ORG, "Platform", "", "root")
    await set_team_member(None, ORG, team.id, "bob", "member")
    agent = await save_persona(
        None,
        ORG,
        "root",
        True,
        AgentPersonaModel(
            id="team-turn-agent",
            name="Team agent",
            role_title="Engineer",
            system_prompt="Work.",
            scope="team",
            team_id=team.id,
        ),
    )
    outcome = await run_turn(ORG, user="bob", metadata={"agent_id": agent.id})
    assert {scope.team_id for scope in outcome.model.scopes} == {team.id}
    personal = await run_turn(ORG, user="bob")
    assert {scope.team_id for scope in personal.model.scopes} == {None}


ADMIN = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "root", "X-User-Role": "admin"}
CAROL = {**ADMIN, "X-Tenant-User-Id": "carol", "X-User-Role": "member"}
BOB = {**ADMIN, "X-Tenant-User-Id": "bob", "X-User-Role": "member"}


def _team(client: TestClient) -> str:
    team_id = str(client.post("/v1/teams", json={"name": "Platform"}, headers=ADMIN).json()["id"])
    client.put(f"/v1/teams/{team_id}/members/carol", json={"role": "admin"}, headers=ADMIN)
    client.put(f"/v1/teams/{team_id}/members/bob", json={"role": "member"}, headers=ADMIN)
    return team_id


def test_team_administrators_manage_the_teams_keys(client: TestClient) -> None:
    team_id = _team(client)
    path = f"/v1/teams/{team_id}/credentials"
    key = {"provider": "anthropic", "api_key": "sk-team-key-0001", "label": "Platform"}
    assert client.post(path, json=key, headers=BOB).status_code == 403
    created = client.post(path, json=key, headers=CAROL)
    assert created.status_code == 200, created.text
    view = created.json()
    assert (view["owner"], view["team_id"]) == ("team", team_id)
    assert "sk-team" not in created.text
    assert [c["id"] for c in client.get(path, headers=CAROL).json()] == [view["id"]]
    assert client.get(path, headers=BOB).status_code == 403
    internal = {**key, "api_base": "http://10.0.0.5:8000"}
    assert client.post(path, json=internal, headers=CAROL).status_code == 422
    owned = client.get(f"/v1/teams/{team_id}/members", headers=CAROL).json()["owned"]
    assert owned == {"credentials": 1}
    assert client.delete(f"{path}/{view['id']}", headers=BOB).status_code == 403
    assert client.delete(f"{path}/{view['id']}", headers=CAROL).json() == {"deleted": True}


def test_a_team_key_binds_to_the_teams_agents_only(client: TestClient) -> None:
    team_id = _team(client)
    key = client.post(
        f"/v1/teams/{team_id}/credentials",
        json={"provider": "anthropic", "api_key": "sk-team-key-0002"},
        headers=CAROL,
    ).json()
    binding = [{"provider": "anthropic", "credential_id": key["id"]}]
    agent = {"name": "Team agent", "role_title": "Engineer", "system_prompt": "Work."}
    team_agent = client.post(
        "/v1/agents",
        json={
            **agent,
            "id": "ta",
            "scope": "team",
            "team_id": team_id,
            "credential_bindings": binding,
        },
        headers=CAROL,
    )
    assert team_agent.status_code == 200, team_agent.text
    org_agent = client.post(
        "/v1/agents",
        json={**agent, "id": "oa", "scope": "org", "credential_bindings": binding},
        headers=ADMIN,
    )
    assert org_agent.status_code == 403
    bindable = client.get("/v1/agents/ta/bindable-credentials", headers=CAROL)
    assert bindable.status_code == 200
    assert [c["id"] for c in bindable.json()] == [key["id"]]
    assert client.get("/v1/agents/ta/bindable-credentials", headers=BOB).status_code == 403
    # An organization administrator outside the team manages its agents too.
    by_admin = client.get("/v1/agents/ta/bindable-credentials", headers=ADMIN)
    assert by_admin.status_code == 200 and key["id"] in {c["id"] for c in by_admin.json()}


def test_team_administrators_add_limits_that_only_tighten(client: TestClient) -> None:
    team_id = _team(client)
    path = f"/v1/teams/{team_id}/spend-limits"
    limit = {"subject_kind": "team", "subject_id": team_id, "period": "month", "amount_usd": 50}
    assert client.post(path, json=limit, headers=BOB).status_code == 403
    created = client.post(path, json=limit, headers=CAROL)
    assert created.status_code == 200, created.text
    assert created.json()["team_id"] == team_id
    org_wide = {**limit, "subject_kind": "org", "subject_id": None}
    assert client.post(path, json=org_wide, headers=CAROL).status_code == 422
    other = {**limit, "subject_id": "team_other"}
    assert client.post(path, json=other, headers=CAROL).status_code == 422
    # The organization's own limit on the team is listed for the team, but not theirs to change.
    by_org = client.post(
        "/v1/admin/spend-limits", json={**limit, "amount_usd": 500}, headers=ADMIN
    ).json()
    listed = {item["id"] for item in client.get(path, headers=CAROL).json()}
    assert {created.json()["id"], by_org["id"]} <= listed
    assert client.delete(f"{path}/{by_org['id']}", headers=CAROL).status_code == 404
    assert (
        client.patch(
            f"{path}/{created.json()['id']}", json={"amount_usd": 20}, headers=CAROL
        ).json()["amount_usd"]
        == 20
    )
    assert client.delete(f"{path}/{created.json()['id']}", headers=CAROL).status_code == 200
