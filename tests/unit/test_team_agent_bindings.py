"""Credentials on a team's agent: its administrators keep what an organization administrator
bound, and bind no organization key themselves."""

from __future__ import annotations

import pytest
from api.models import AgentPersonaModel, CredentialBindingModel
from api.services.model_credentials import ModelCredentialStore, NewCredential
from api.services.personas import PersonaError, save_persona
from api.services.teams import create_team, set_team_member

from specifications.interfaces.ownership import Viewer

ORG = "team_bindings"


def _carol(team_id: str) -> Viewer:
    return Viewer("carol", teams=frozenset({team_id}), admin_teams=frozenset({team_id}))


async def test_a_team_administrator_keeps_an_organization_key_but_cannot_bind_one() -> None:
    team = await create_team(None, ORG, "Platform", "", "root")
    await set_team_member(None, ORG, team.id, "carol", "admin")
    store = ModelCredentialStore(None)
    pro = await store.create(ORG, NewCredential("org", None, "anthropic", "sk-pro-000001", "Pro"))
    spare = await store.create(ORG, NewCredential("org", None, "openai", "sk-oai-000001", "OAI"))
    agent = AgentPersonaModel(
        id="team-agent",
        name="Team agent",
        role_title="Engineer",
        system_prompt="Work.",
        scope="team",
        team_id=team.id,
        credential_bindings=[CredentialBindingModel(provider="anthropic", credential_id=pro.id)],
    )
    saved = await save_persona(None, ORG, "root", True, agent, credentials=store)

    edited = saved.model_copy(update={"description": "Kept the key"})
    kept = await save_persona(
        None, ORG, "carol", False, edited, credentials=store, viewer=_carol(team.id)
    )
    assert [b.credential_id for b in kept.credential_bindings] == [pro.id]

    added = kept.model_copy(
        update={
            "credential_bindings": [
                *kept.credential_bindings,
                CredentialBindingModel(provider="openai", credential_id=spare.id),
            ]
        }
    )
    with pytest.raises(PersonaError) as refused:
        await save_persona(
            None, ORG, "carol", False, added, credentials=store, viewer=_carol(team.id)
        )
    assert refused.value.status_code == 403
    assert "organization administrators" in refused.value.detail
