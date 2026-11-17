"""Change proposals: the agent stages, a person who may edit the target decides."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from api.main import app
from api.models import AgentPersonaModel
from api.services.audit import AuditService
from api.services.personas import find_agent, save_persona
from api.services.proposals import BoundProposals, proposal_store
from api.services.settings import reset_memory_settings, save_org_config
from api.services.skills_lookup import _memory_skills
from fastapi.testclient import TestClient
from turn_harness import run_turn

from specifications.interfaces.ownership import Viewer
from specifications.interfaces.proposals import ProposalEdit

PROMPT = "You answer data questions.\nAlways answer in English."


def _org() -> str:
    return f"prop_{uuid.uuid4().hex[:8]}"


async def _agent(org: str, **fields: Any) -> str:
    persona = AgentPersonaModel(
        id=f"persona-{org}",
        name="Data assistant",
        role_title="Data assistant",
        system_prompt=PROMPT,
        scope="org",
        **fields,
    )
    return (await save_persona(None, org, "admin", True, persona)).id


def _bound(org: str, agent_id: str) -> BoundProposals:
    return BoundProposals(
        db_mgr=None,
        org_id=org,
        agent_id=agent_id,
        session_id="s1",
        requested_by="slack:<@U0OPERATOR>",
        audit=AuditService(None),
    )


def _edit() -> list[ProposalEdit]:
    return [ProposalEdit("Always answer in English.", "Answer in the language you are asked in.")]


async def test_a_proposal_in_a_turn_is_stored_and_changes_nothing() -> None:
    org = _org()
    agent_id = await _agent(org, proposes_changes=True)

    outcome = await run_turn(
        org,
        metadata={"agent_id": agent_id},
        script=[
            ("load_tools", {"names": ["propose_change"]}),
            (
                "propose_change",
                {
                    "target": "instructions",
                    "edits": [
                        {
                            "old_text": "Always answer in English.",
                            "new_text": "Answer in the language you are asked in.",
                        }
                    ],
                    "summary": "Answer in the asker's language",
                    "rationale": "The operator asked for it in the review channel.",
                },
            ),
        ],
    )

    last = [r for r in outcome.model.requests if r.tools][-1]
    results = "\n".join(m.content or "" for m in last.messages if m.role == "tool")
    assert "is waiting for review" in results
    pending = await proposal_store(None).list_for_org(org, status="pending", agent_id=None, limit=5)
    assert [p.summary for p in pending] == ["Answer in the asker's language"]
    agent = await find_agent(None, org, Viewer(user_id="", is_org_admin=True), agent_id)
    assert agent is not None and agent.system_prompt == PROMPT


async def test_an_agent_without_the_switch_cannot_propose() -> None:
    org = _org()
    agent_id = await _agent(org)

    outcome = await run_turn(
        org,
        metadata={"agent_id": agent_id},
        script=[
            (
                "propose_change",
                {"target": "instructions", "edits": [], "summary": "x", "rationale": "y"},
            )
        ],
    )

    request = next(r for r in outcome.model.requests if r.tools)
    system = "\n".join(m.content or "" for m in request.messages if m.role == "system")
    assert (
        "propose_change" not in {t.name for t in request.tools} and "propose_change" not in system
    )
    assert await proposal_store(None).list_for_org(org, status=None, agent_id=None, limit=5) == []


async def test_credentials_unknown_text_and_no_op_edits_are_refused() -> None:
    org = _org()
    proposals = _bound(org, await _agent(org, proposes_changes=True))

    secret = await proposals.propose(
        "instructions",
        "",
        [ProposalEdit("Always answer in English.", "Use token xoxb-1234567890-abcdefghij")],
        "s",
        "r",
    )
    missing = await proposals.propose(
        "instructions", "", [ProposalEdit("not in the prompt", "x")], "s", "r"
    )
    same = await proposals.propose(
        "instructions",
        "",
        [ProposalEdit("Always answer in English.", "Always answer in English.")],
        "s",
        "r",
    )

    assert "credential" in secret
    assert "was not found" in missing
    assert "change nothing" in same
    assert await proposal_store(None).list_for_org(org, status=None, agent_id=None, limit=5) == []


@pytest.mark.setting("governance.proposal_max_pending")
async def test_an_agent_cannot_pile_up_pending_proposals() -> None:
    org = _org()
    await save_org_config(None, org, "governance", {"proposal_max_pending": 2}, [])
    proposals = _bound(org, await _agent(org, proposes_changes=True))

    outcomes = [
        await proposals.propose("instructions", "", _edit(), f"change {n}", "r") for n in range(3)
    ]

    assert all("waiting for review" in o for o in outcomes[:2])
    assert "2 proposals waiting" in outcomes[2]


@pytest.mark.setting("governance.proposal_max_change_chars")
async def test_a_change_larger_than_the_organization_allows_is_refused() -> None:
    org = _org()
    await save_org_config(None, org, "governance", {"proposal_max_change_chars": 200}, [])
    proposals = _bound(org, await _agent(org, proposes_changes=True))

    outcome = await proposals.propose(
        "instructions", "", [ProposalEdit("Always answer in English.", "word " * 100)], "s", "r"
    )

    assert "over the 200 allowed" in outcome


@pytest.fixture
def client() -> Iterator[TestClient]:
    reset_memory_settings()
    with TestClient(app) as c:
        yield c


def _as(role: str, org: str, user: str = "u1") -> dict[str, str]:
    return {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": user, "X-User-Role": role}


async def _pending(org: str, agent_id: str, edits: list[ProposalEdit] | None = None) -> str:
    message = await _bound(org, agent_id).propose("instructions", "", edits or _edit(), "s", "r")
    assert "waiting for review" in message, message
    # The id from the answer itself: two proposals made within one second tie on created_at.
    return message.split()[1]


async def test_only_who_may_edit_the_agent_sees_and_decides_its_proposals(
    client: TestClient,
) -> None:
    org = _org()
    agent_id = await _agent(org, proposes_changes=True)
    proposal_id = await _pending(org, agent_id)

    member = client.get("/v1/proposals", headers=_as("member", org, "u2"))
    assert member.status_code == 200 and member.json()["proposals"] == []
    refused = client.post(
        f"/v1/proposals/{proposal_id}/approve", json={}, headers=_as("member", org, "u2")
    )
    assert refused.status_code == 404

    listed = client.get("/v1/proposals", headers=_as("admin", org)).json()["proposals"]
    assert [p["id"] for p in listed] == [proposal_id]
    detail = client.get(f"/v1/proposals/{proposal_id}", headers=_as("admin", org)).json()
    assert (
        detail["base_content"] == PROMPT
        and "language you are asked in" in detail["proposed_content"]
    )

    approved = client.post(
        f"/v1/proposals/{proposal_id}/approve", json={"note": "good"}, headers=_as("admin", org)
    )
    assert approved.status_code == 200, approved.text
    agent = await find_agent(None, org, Viewer(user_id="", is_org_admin=True), agent_id)
    assert agent is not None and "language you are asked in" in agent.system_prompt
    again = client.post(f"/v1/proposals/{proposal_id}/reject", json={}, headers=_as("admin", org))
    assert again.status_code == 409


async def test_an_edited_approval_saves_the_reviewers_text(client: TestClient) -> None:
    org = _org()
    agent_id = await _agent(org, proposes_changes=True)
    proposal_id = await _pending(org, agent_id)

    client.post(
        f"/v1/proposals/{proposal_id}/approve",
        json={"content": "REVIEWED PROMPT"},
        headers=_as("admin", org),
    )

    agent = await find_agent(None, org, Viewer(user_id="", is_org_admin=True), agent_id)
    assert agent is not None and agent.system_prompt == "REVIEWED PROMPT"


async def test_a_proposal_is_rebased_on_a_changed_target_or_refused_on_conflict(
    client: TestClient,
) -> None:
    org = _org()
    agent_id = await _agent(org, proposes_changes=True)
    rebased = await _pending(org, agent_id)
    conflicting = await _pending(
        org, agent_id, [ProposalEdit("You answer data questions.", "You answer questions.")]
    )
    viewer = Viewer(user_id="admin", is_org_admin=True)
    agent = await find_agent(None, org, viewer, agent_id)
    assert agent is not None
    await save_persona(
        None,
        org,
        "admin",
        True,
        agent.model_copy(
            update={
                "system_prompt": "You answer data questions, briefly.\nAlways answer in English."
            }
        ),
    )

    ok = client.post(f"/v1/proposals/{rebased}/approve", json={}, headers=_as("admin", org))
    clash = client.post(f"/v1/proposals/{conflicting}/approve", json={}, headers=_as("admin", org))

    assert ok.status_code == 200, ok.text
    assert ok.json()["proposed_content"] == (
        "You answer data questions, briefly.\nAnswer in the language you are asked in."
    )
    assert clash.status_code == 409 and "no longer apply" in clash.json()["detail"]


async def test_a_skill_proposal_is_decided_by_who_may_edit_the_skill(client: TestClient) -> None:
    org = _org()
    _memory_skills.setdefault(org, {})["skill-traps"] = {
        "id": "skill-traps",
        "name": "query-traps",
        "title": "query-traps",
        "description": "Traps",
        "body": "Use dbt models only.",
        "runtime": "text",
        "is_active": True,
        "scope": "org",
        "user_id": None,
        "team_id": None,
    }
    agent_id = await _agent(org, proposes_changes=True)
    message = await _bound(org, agent_id).propose(
        "skill",
        "query-traps",
        [ProposalEdit("Use dbt models only.", "Use dbt models only; the archive schema is stale.")],
        "archive schema trap",
        "Found while answering a request",
    )
    assert "waiting for review" in message, message
    proposal_id = (
        await proposal_store(None).list_for_org(org, status="pending", agent_id=None, limit=1)
    )[0].id

    assert (
        client.post(
            f"/v1/proposals/{proposal_id}/approve", json={}, headers=_as("member", org, "u2")
        ).status_code
        == 404
    )
    approved = client.post(
        f"/v1/proposals/{proposal_id}/approve", json={}, headers=_as("admin", org)
    )

    assert approved.status_code == 200, approved.text
    assert "archive schema" in _memory_skills[org]["skill-traps"]["body"]
