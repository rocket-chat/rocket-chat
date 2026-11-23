"""A change to a skill that follows an external source is a pull request on its repository,
opened with the linked GitHub account of whoever asks; nothing changes on the platform."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from api.main import app
from api.models import AgentPersonaModel
from api.services.audit import AuditService
from api.services.personas import save_persona
from api.services.proposals import BoundProposals, proposal_store
from api.services.skill_store import SkillContent
from api.services.source_changes import rewrite_document
from fastapi.testclient import TestClient

from specifications.interfaces.proposals import ChangeProposal, ProposalEdit
from tests.support.fake_source import FakeRepository, install, skill_md
from tests.turn_harness import run_turn

DOCUMENT = """---
# Owned by the data team
name: logs
description: Read logs
allowed-tools: Bash
---

Old steps.
"""


def _headers(org: str, user: str, role: str = "admin") -> dict[str, str]:
    return {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": user, "X-User-Role": role}


@pytest.fixture
def org() -> str:
    return f"chg_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def repo() -> FakeRepository:
    repo = FakeRepository()
    repo.commit({"logs/SKILL.md": DOCUMENT, "other/SKILL.md": skill_md("other", "Other")})
    install(app, repo, linked=("root",))
    return repo


def _imported(client: TestClient, org: str) -> dict[str, Any]:
    admin = _headers(org, "root")
    source = client.post(
        "/v1/external-sources", json={"repository": "acme/agent-skills"}, headers=admin
    ).json()
    imported = client.post(
        f"/v1/external-sources/{source['id']}/import",
        json={"paths": ["logs"], "scope": "org"},
        headers=admin,
    ).json()["imported"]
    return dict(imported[0])


def _change(client: TestClient, org: str, skill_id: str, user: str = "root", **body: Any) -> Any:
    return client.post(
        f"/v1/skills/{skill_id}/change-request",
        json={"description": "Read logs", "body": "New steps.", **body},
        headers=_headers(org, user, "admin" if user == "root" else "member"),
    )


def test_a_body_change_keeps_the_front_matter_as_written() -> None:
    current = SkillContent("logs", "Read logs", "Old steps.")

    rewritten = rewrite_document(DOCUMENT, current, SkillContent("logs", "Read logs", "New steps."))

    assert rewritten.startswith("---\n# Owned by the data team\nname: logs\n")
    assert rewritten.endswith("\nNew steps.\n") and "Old steps." not in rewritten


def test_a_description_change_rewrites_the_front_matter_and_keeps_other_keys() -> None:
    current = SkillContent("logs", "Read logs", "Old steps.")

    rewritten = rewrite_document(
        DOCUMENT, current, SkillContent("logs", "Read service logs", "Old steps.")
    )

    assert "description: Read service logs" in rewritten
    assert "allowed-tools: Bash" in rewritten and rewritten.index("name:") < rewritten.index(
        "allowed-tools:"
    )


def test_proposing_a_change_opens_a_pull_request_as_the_member(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    skill = _imported(client, org)

    response = _change(client, org, skill["id"], summary="Clarify the log steps")

    assert response.status_code == 200, response.text
    assert response.json()["url"] == "https://github.com/acme/agent-skills/pull/1"
    proposed = repo.proposals[0]
    assert (proposed.user_id, proposed.base, proposed.path) == ("root", "main", "logs/SKILL.md")
    assert proposed.title == "Clarify the log steps"
    assert proposed.branch.startswith("rocket/skill-logs-")
    assert (
        proposed.content.endswith("\nNew steps.\n")
        and "# Owned by the data team" in proposed.content
    )
    skills = client.get("/v1/skills", headers=_headers(org, "root")).json()
    assert next(s for s in skills if s["id"] == skill["id"])["body"] == "Old steps."


def test_without_a_linked_github_account_the_member_is_told_to_link_one(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    skill = _imported(client, org)

    response = _change(client, org, skill["id"], user="bob")

    assert response.status_code == 409 and "Link your GitHub account" in response.json()["detail"]
    assert repo.proposals == []


def test_a_change_made_in_the_repository_since_the_last_read_is_not_overwritten(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    skill = _imported(client, org)
    repo.change("logs/SKILL.md", DOCUMENT.replace("Old steps.", "Someone else's steps."))

    response = _change(client, org, skill["id"])

    assert response.status_code == 409 and "changed since" in response.json()["detail"]


def test_a_skill_without_a_source_is_edited_directly(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    local = client.post(
        "/v1/skills",
        json={"name": "mine", "body": "x", "scope": "org"},
        headers=_headers(org, "root"),
    ).json()

    response = _change(client, org, local["id"])

    assert response.status_code == 409 and "has no source" in response.json()["detail"]


def test_a_skill_the_caller_cannot_see_is_not_found(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    response = _change(client, org, "skill-nowhere")

    assert response.status_code == 404


def test_approving_an_agents_proposal_on_a_synchronised_skill_opens_a_pull_request(
    client: TestClient, org: str, repo: FakeRepository
) -> None:
    skill = _imported(client, org)
    admin = _headers(org, "root")
    agent = client.post(
        "/v1/agents",
        json={
            "id": f"persona-{org}",
            "name": "Ops",
            "role_title": "Ops",
            "system_prompt": "You help.",
            "model": "",
            "scope": "org",
            "proposes_changes": True,
            "skill_ids": [skill["id"]],
        },
        headers=admin,
    ).json()
    bound = BoundProposals(
        db_mgr=getattr(app.state, "db_manager", None),
        org_id=org,
        agent_id=agent["id"],
        session_id=None,
        requested_by="root",
        audit=AuditService(None),
    )
    assert client.portal is not None
    staged = client.portal.call(
        bound.propose,
        "skill",
        "logs",
        [ProposalEdit("Old steps.", "Better steps.")],
        "Better log steps",
        "The operator said so.",
    )
    proposal_id = staged.split()[1]

    approved = client.post(f"/v1/proposals/{proposal_id}/approve", json={}, headers=admin)

    assert approved.status_code == 200, approved.text
    assert approved.json()["change_request_url"] == "https://github.com/acme/agent-skills/pull/1"
    assert repo.proposals[0].content.endswith("\nBetter steps.\n")
    assert "The operator said so." in repo.proposals[0].body
    skills = client.get("/v1/skills", headers=admin).json()
    assert next(s for s in skills if s["id"] == skill["id"])["body"] == "Old steps."
    mine = client.portal.call(bound.list_mine, "approved", 5)
    assert mine[0].change_request_url.endswith("/pull/1")


async def test_the_agent_reads_the_pull_request_of_its_approved_proposal() -> None:
    org = f"chg_{uuid.uuid4().hex[:8]}"
    agent = await save_persona(
        None,
        org,
        "admin",
        True,
        AgentPersonaModel(
            id=f"persona-{org}",
            name="Ops",
            role_title="Ops",
            system_prompt="You help.",
            scope="org",
            proposes_changes=True,
        ),
    )
    store = proposal_store(None)
    staged = await store.create(
        ChangeProposal(
            id=f"prop_{org}",
            org_id=org,
            agent_id=agent.id,
            target_kind="skill",
            target_id="skill-1",
            target_name="logs",
            summary="Better log steps",
            rationale="r",
            base_content="a",
            proposed_content="b",
        )
    )
    await store.decide(
        org,
        staged.id,
        status="approved",
        decided_by="root",
        note="",
        applied_content="b",
        change_request_url="https://github.com/acme/agent-skills/pull/9",
    )

    outcome = await run_turn(
        org,
        metadata={"agent_id": agent.id},
        script=[
            ("load_tools", {"names": ["list_my_proposals"]}),
            ("list_my_proposals", {"status": "approved"}),
        ],
    )

    results = [
        m.content or "" for r in outcome.model.requests for m in r.messages if m.role == "tool"
    ]
    assert any("pull request https://github.com/acme/agent-skills/pull/9" in r for r in results)
