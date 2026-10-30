"""Who may use an agent, and what a whole turn does about it (P3.3, P2.4)."""

from __future__ import annotations

import pytest
from api.models import AgentPersonaModel, CredentialBindingModel
from api.services.agent_access import (
    AgentAccessDenied,
    Principal,
    may_use,
    reset_memory_grants,
    set_grants,
)
from api.services.model_credentials import ModelCredentialStore, NewCredential
from api.services.personas import get_persona, list_personas, save_persona
from api.services.settings import reset_memory_settings
from api.services.turn_service import permission_mode_for

from specifications.interfaces.events import EventType
from tests.turn_harness import run_turn


@pytest.fixture(autouse=True)
def _clean() -> None:
    reset_memory_grants()
    reset_memory_settings()


def _persona(org: str, name: str, **extra: object) -> AgentPersonaModel:
    return AgentPersonaModel(
        id=f"agent-{org}-{name.lower()}",
        name=name,
        role_title="Specialist",
        system_prompt=f"{name.upper()}-MARKER",
        model="",
        scope="org",
        **extra,  # type: ignore[arg-type]
    )


MEMBER = Principal("alice", ("member",))
ADMIN = Principal("root", ("admin", "member"))
GUEST = Principal("slack:T1:U1", ("guest",))


async def test_no_grants_serves_every_member_but_not_guests() -> None:
    agent = _persona("acc1", "Pro")
    assert await may_use(None, "acc1", MEMBER, agent)
    assert not await may_use(None, "acc1", GUEST, agent)


async def test_grants_narrow_access_and_admins_keep_it() -> None:
    agent = _persona("acc2", "Pro")
    await set_grants(None, "acc2", agent.id, [("member", "bob"), ("role", "viewer")], "root")
    assert not await may_use(None, "acc2", MEMBER, agent)
    assert await may_use(None, "acc2", Principal("bob", ("member",)), agent)
    assert await may_use(None, "acc2", Principal("vera", ("viewer",)), agent)
    assert await may_use(None, "acc2", ADMIN, agent)
    await set_grants(None, "acc2", agent.id, [("everyone", ""), ("guests", "")], "root")
    assert await may_use(None, "acc2", MEMBER, agent)
    assert await may_use(None, "acc2", GUEST, agent)


async def test_personal_and_disabled_agents() -> None:
    mine = AgentPersonaModel(
        id="p1", name="Mine", role_title="x", system_prompt="x", scope="user", user_id="alice"
    )
    assert await may_use(None, "acc3", MEMBER, mine)
    assert not await may_use(None, "acc3", ADMIN, mine)
    off = _persona("acc3", "Off", enabled=False)
    assert not await may_use(None, "acc3", ADMIN, off)


async def test_lookup_and_listing_apply_the_check() -> None:
    saved = await save_persona(None, "acc4", "root", True, _persona("acc4", "Pro"))
    await set_grants(None, "acc4", saved.id, [("member", "bob")], "root")
    with pytest.raises(AgentAccessDenied):
        await get_persona(None, "acc4", "alice", saved.id, roles=["member"])
    assert await get_persona(None, "acc4", "bob", saved.id, roles=["member"]) is not None
    assert saved.id not in {p.id for p in await list_personas(None, "acc4", "alice", ["member"])}
    assert await get_persona(None, "acc4", "", saved.id, enforce_access=False) is not None


async def test_a_turn_by_someone_without_access_is_refused_with_a_typed_event() -> None:
    org = "acc_turn"
    saved = await save_persona(None, org, "root", True, _persona(org, "Pro"))
    await set_grants(None, org, saved.id, [("member", "bob")], "root")
    outcome = await run_turn(org, user="bob", author="alice", metadata={"agent_id": saved.id})
    denied = [e for e in outcome.events if e.event_type == EventType.AGENT_ACCESS_DENIED]
    assert denied and denied[0].payload["agent_id"] == saved.id
    errors = [e for e in outcome.events if e.event_type == EventType.ERROR]
    assert errors and errors[-1].payload["error_kind"] == "access"
    assert not outcome.model.requests, "nothing was asked of the model"


async def test_the_requester_and_the_agents_bindings_reach_every_model_call() -> None:
    org = "acc_bind"
    store = ModelCredentialStore(None)
    credential = await store.create(
        org,
        NewCredential("org", None, "anthropic", "sk-pro-0000001", "Pro", restricted_to_agents=True),
    )
    persona = _persona(
        org,
        "Pro",
        credential_bindings=[
            CredentialBindingModel(provider="anthropic", credential_id=credential.id)
        ],
        strict_binding=True,
    )
    saved = await save_persona(None, org, "root", True, persona, credentials=store)
    outcome = await run_turn(org, user="owner", author="alice", metadata={"agent_id": saved.id})
    scope = outcome.model.scopes[0]
    assert scope.user_id == "owner", "the session's owner stays the tenant of record"
    assert scope.requester_user_id == "alice"
    assert scope.agent_id == saved.id
    assert [(b.provider, b.credential_id) for b in scope.credential_bindings] == [
        ("anthropic", credential.id)
    ]
    assert scope.strict_binding and scope.turn_id


@pytest.mark.setting("user_preferences.permission_mode")
async def test_bypass_is_the_requesters_capability() -> None:
    from api.services.settings import save_org_config

    org = "acc_bypass"
    assert await permission_mode_for(None, org, "alice", None, "bypass") == "ask"
    await save_org_config(None, org, "access", {"use_bypass_mode": ["member"]}, [])
    assert await permission_mode_for(None, org, "alice", None, "bypass") == "bypass"
    await save_org_config(
        None, org, "user_preferences", {"permission_mode": "plan"}, ["permission_mode"]
    )
    assert await permission_mode_for(None, org, "alice", None, "bypass") == "plan"
