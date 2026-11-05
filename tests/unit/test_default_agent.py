"""A member's default agent: their own, else their only team's, else the organization's, else
none; a level whose agent they may not use is skipped. It starts API sessions created without
an agent, and Slack direct messages to a bot without an agent of its own."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from api.bootstrap import slack as slack_bootstrap
from api.models import AgentPersonaModel
from api.services.agent_access import reset_memory_grants, set_grants
from api.services.default_agent import resolve_default_agent
from api.services.personas import delete_persona, save_persona
from api.services.settings import save_org_config, save_team_config, save_user_config
from api.services.teams import create_team, set_team_member
from api.store import InMemorySessionStore
from config_engine import CascadingSettingsResolver
from fastapi.testclient import TestClient

from specifications.interfaces.agent import TurnOverlay
from specifications.interfaces.session import SessionRecord, SessionSource

ORG = "dflt"
DOMAIN = "user_preferences"
ADMIN = {"X-Tenant-Org-Id": ORG, "X-Tenant-User-Id": "root", "X-User-Role": "admin"}
CAROL = {**ADMIN, "X-Tenant-User-Id": "carol", "X-User-Role": "member"}  # team administrator
BOB = {**ADMIN, "X-Tenant-User-Id": "bob", "X-User-Role": "member"}  # team member
DAVE = {**ADMIN, "X-Tenant-User-Id": "dave", "X-User-Role": "member"}  # in no team
VERA = {**ADMIN, "X-Tenant-User-Id": "vera", "X-User-Role": "viewer"}


@pytest.fixture(autouse=True)
def _clean() -> None:
    reset_memory_grants()


async def _agent(name: str, org: str = ORG, **owner: Any) -> AgentPersonaModel:
    return await save_persona(
        None,
        org,
        str(owner.pop("by", "root")),
        True,
        AgentPersonaModel(
            id=f"{org}-{name.lower()}",
            name=name,
            role_title="Engineer",
            system_prompt="Work.",
            **{"scope": "org", **owner},
        ),
    )


@pytest.mark.setting("user_preferences.default_agent")
async def test_the_member_then_their_only_team_then_the_organization_decides() -> None:
    org_agent, team_agent, own = await _agent("Org"), await _agent("Team"), await _agent("Own")
    team = await create_team(None, ORG, "Platform", "", "root")
    await set_team_member(None, ORG, team.id, "bob", "member")

    nothing = await resolve_default_agent(None, ORG, "bob")
    assert (nothing.agent_id, nothing.source) == (None, "platform")

    await save_org_config(None, ORG, DOMAIN, {"default_agent": org_agent.id}, [])
    assert (await resolve_default_agent(None, ORG, "bob")).agent_id == org_agent.id

    await save_team_config(None, ORG, team.id, DOMAIN, {"default_agent": team_agent.id})
    in_team = await resolve_default_agent(None, ORG, "bob")
    assert (in_team.agent_id, in_team.source, in_team.team_id) == (
        team_agent.id,
        f"team:{team.id}",
        team.id,
    )

    await save_user_config(None, ORG, "bob", DOMAIN, {"default_agent": own.id})
    mine = await resolve_default_agent(None, ORG, "bob")
    assert (mine.agent_id, mine.source) == (own.id, "member")
    inherited = await resolve_default_agent(None, ORG, "bob", include_member=False)
    assert (inherited.agent_id, inherited.source) == (team_agent.id, f"team:{team.id}")


async def test_a_member_of_several_teams_skips_the_team_level() -> None:
    org_agent, team_agent = await _agent("Org"), await _agent("Team")
    first = await create_team(None, ORG, "First", "", "root")
    second = await create_team(None, ORG, "Second", "", "root")
    await set_team_member(None, ORG, first.id, "bob", "member")
    await save_org_config(None, ORG, DOMAIN, {"default_agent": org_agent.id}, [])
    await save_team_config(None, ORG, first.id, DOMAIN, {"default_agent": team_agent.id})
    assert (await resolve_default_agent(None, ORG, "bob")).agent_id == team_agent.id

    await set_team_member(None, ORG, second.id, "bob", "member")
    choice = await resolve_default_agent(None, ORG, "bob")
    assert (choice.agent_id, choice.source, choice.team_id) == (org_agent.id, "org", None)


async def test_a_default_the_member_may_no_longer_use_falls_to_the_next_level() -> None:
    org_agent, gone = await _agent("Org"), await _agent("Gone")
    granted = await _agent("Granted")
    others = await _agent("Theirs", scope="user", user_id="dave", by="dave")
    team = await create_team(None, ORG, "Platform", "", "root")
    await set_team_member(None, ORG, team.id, "bob", "member")
    await save_org_config(None, ORG, DOMAIN, {"default_agent": org_agent.id}, [])
    await save_team_config(None, ORG, team.id, DOMAIN, {"default_agent": granted.id})
    await set_grants(None, ORG, granted.id, [("member", "carol")], "root")
    await save_user_config(None, ORG, "bob", DOMAIN, {"default_agent": others.id})

    choice = await resolve_default_agent(None, ORG, "bob")
    assert (choice.agent_id, choice.source) == (org_agent.id, "org")
    assert set(choice.skipped) == {"member", f"team:{team.id}"}

    await delete_persona(None, ORG, "root", True, org_agent.id)
    await save_user_config(None, ORG, "bob", DOMAIN, {"default_agent": gone.id})
    await delete_persona(None, ORG, "root", True, gone.id)
    none_left = await resolve_default_agent(None, ORG, "bob")
    assert (none_left.agent_id, none_left.source) == (None, "platform")
    assert "org" in none_left.skipped


async def test_the_organizations_lock_overrules_the_team_and_the_member() -> None:
    org_agent, own = await _agent("Org"), await _agent("Own")
    await save_org_config(None, ORG, DOMAIN, {"default_agent": org_agent.id}, ["default_agent"])
    await save_user_config(None, ORG, "bob", DOMAIN, {"default_agent": own.id})
    choice = await resolve_default_agent(None, ORG, "bob")
    assert (choice.agent_id, choice.source, choice.locked, choice.skipped) == (
        org_agent.id,
        "org",
        True,
        (),
    )
    await delete_persona(None, ORG, "root", True, org_agent.id)
    unusable = await resolve_default_agent(None, ORG, "bob")
    assert (unusable.agent_id, unusable.source, unusable.skipped) == (None, "platform", ("org",))


def test_the_resolver_applies_an_acceptance_test_to_its_key_only() -> None:
    resolved = CascadingSettingsResolver().resolve(
        DOMAIN,
        org_config={"default_agent": "a-org", "git_branch_prefix": "org/"},
        user_overrides={"default_agent": "a-mine", "git_branch_prefix": "me/"},
        accept={"default_agent": lambda value: value != "a-mine"},
    )
    assert resolved.effective["default_agent"] == "a-org"
    assert resolved.provenance["default_agent"].refused == ("member",)
    assert resolved.effective["git_branch_prefix"] == "me/"


# -- the routes ------------------------------------------------------------------------------


def _org_agent(client: TestClient, name: str, **extra: Any) -> str:
    body = {
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "role_title": "Engineer",
        "system_prompt": "Work.",
        **extra,
    }
    saved = client.post("/v1/agents", json=body, headers=ADMIN)
    assert saved.status_code == 200, saved.text
    return str(saved.json()["id"])


def _team(client: TestClient) -> str:
    team_id = str(client.post("/v1/teams", json={"name": "Platform"}, headers=ADMIN).json()["id"])
    client.put(f"/v1/teams/{team_id}/members/carol", json={"role": "admin"}, headers=ADMIN)
    client.put(f"/v1/teams/{team_id}/members/bob", json={"role": "member"}, headers=ADMIN)
    return team_id


def _set_org(client: TestClient, agent_id: str, headers: dict[str, str] = ADMIN) -> Any:
    return client.put(
        f"/v1/admin/settings/{DOMAIN}",
        json={"config": {"default_agent": agent_id}, "locked_keys": []},
        headers=headers,
    )


def test_each_level_is_set_by_those_who_decide_it(client: TestClient) -> None:
    org_agent, team_agent = _org_agent(client, "Org agent"), _org_agent(client, "Team agent")
    team_id = _team(client)
    team_path = f"/v1/teams/{team_id}/settings/{DOMAIN}"

    assert _set_org(client, org_agent, BOB).status_code == 403
    assert _set_org(client, org_agent).status_code == 200

    body = {"config": {"default_agent": team_agent}}
    assert client.put(team_path, json=body, headers=BOB).status_code == 403
    assert client.put(team_path, json=body, headers=DAVE).status_code == 403
    assert client.put(team_path, json=body, headers=CAROL).status_code == 200, "team admin"
    assert client.put(team_path, json=body, headers=ADMIN).status_code == 200, "org admin"

    own = {"overrides": {"default_agent": org_agent}}
    assert client.put(f"/v1/settings/{DOMAIN}", json=own, headers=VERA).status_code == 403
    assert client.put(f"/v1/settings/{DOMAIN}", json=own, headers=BOB).status_code == 200

    view = client.get("/v1/me/default-agent", headers=BOB).json()
    assert view["agent_id"] == org_agent and view["source"] == "member"
    assert view["agent_name"] == "Org agent" and view["own_agent_id"] == org_agent
    assert view["inherited"]["agent_id"] == team_agent
    assert view["inherited"]["source"] == f"team:{team_id}" and view["team_id"] == team_id

    assert client.delete(f"/v1/settings/{DOMAIN}/default_agent", headers=BOB).status_code == 200
    assert client.get("/v1/me/default-agent", headers=BOB).json()["agent_id"] == team_agent
    outside = client.get("/v1/me/default-agent", headers=DAVE).json()
    assert (outside["agent_id"], outside["source"], outside["team_id"]) == (org_agent, "org", None)


def test_a_default_naming_an_agent_the_writer_cannot_use_is_refused(client: TestClient) -> None:
    unknown = _set_org(client, "no-such-agent")
    assert unknown.status_code == 422 and unknown.json()["detail"][0]["key"] == "default_agent"
    personal = client.post(
        "/v1/agents",
        json={
            "id": "mine",
            "name": "Mine",
            "role_title": "Me",
            "system_prompt": "Work.",
            "scope": "user",
        },
        headers=ADMIN,
    ).json()["id"]
    assert _set_org(client, personal).status_code == 422, "a personal agent serves one member"

    restricted = _org_agent(client, "Restricted")
    client.put(
        f"/v1/agents/{restricted}/access",
        json={"grants": [{"principal_kind": "member", "principal_id": "carol"}]},
        headers=ADMIN,
    )
    refused = client.put(
        f"/v1/settings/{DOMAIN}", json={"overrides": {"default_agent": restricted}}, headers=BOB
    )
    assert refused.status_code == 422, refused.text


def test_an_api_session_without_an_agent_starts_with_the_default(client: TestClient) -> None:
    org_agent, chosen = _org_agent(client, "Org agent"), _org_agent(client, "Chosen")
    assert _set_org(client, org_agent).status_code == 200

    def created(body: dict[str, Any]) -> dict[str, Any]:
        response = client.post("/v1/sessions", json=body, headers=BOB)
        assert response.status_code == 200, response.text
        return dict(response.json()["metadata"])

    assert created({"title": "Default"})["agent_id"] == org_agent
    assert created({"title": "Chosen", "agent_id": chosen})["agent_id"] == chosen
    assert "agent_id" not in created({"title": "Raw", "mode": "direct"})

    client.delete(f"/v1/agents/{org_agent}", headers=ADMIN)
    assert "agent_id" not in created({"title": "Stale default"}), "skipped, never an error"


# -- Slack direct messages -------------------------------------------------------------------


async def _slack_turn(
    monkeypatch: pytest.MonkeyPatch, metadata: dict[str, Any], overlay: TurnOverlay
) -> TurnOverlay:
    store = InMemorySessionStore()
    await store.create_session(
        SessionRecord(
            session_id="slack-dm",
            tenant_org_id=ORG,
            tenant_user_id="bob",
            title="Slack DM",
            source=SessionSource.SLACK,
            metadata=metadata,
        )
    )
    ran: list[TurnOverlay] = []

    async def run_guarded_turn(**kwargs: Any) -> None:
        ran.append(kwargs["overlay"])

    monkeypatch.setattr(slack_bootstrap, "run_guarded_turn", run_guarded_turn)
    core = SimpleNamespace(
        session_store=store,
        db_manager=None,
        orchestrator=None,
        event_bus=None,
        sandbox_driver=None,
        tool_registry=None,
        git_engine=None,
        attachment_service=None,
        concurrency_gate=None,
        session_locks=None,
    )
    callbacks = slack_bootstrap._SlackCallbacks(core, wait_store=object())  # type: ignore[arg-type]
    await callbacks.run_turn(
        session_id="slack-dm", prompt="hi", overlay=overlay, author_user_id="bob"
    )
    return ran[0]


@pytest.mark.setting("user_preferences.default_agent")
async def test_a_slack_dm_to_a_bot_without_an_agent_runs_the_members_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    own = await _agent("Own")
    await save_user_config(None, ORG, "bob", DOMAIN, {"default_agent": own.id})
    dm = {"slack_is_dm": True}

    assert (await _slack_turn(monkeypatch, dm, TurnOverlay())).default_agent_id == own.id
    bots = TurnOverlay(default_agent_id="bot-agent")
    assert (await _slack_turn(monkeypatch, dm, bots)).default_agent_id == "bot-agent"
    channel = {"slack_is_dm": False}
    assert (await _slack_turn(monkeypatch, channel, TurnOverlay())).default_agent_id is None
    bound = {"slack_is_dm": True, "agent_id": "chosen"}
    assert (await _slack_turn(monkeypatch, bound, TurnOverlay())).default_agent_id is None
