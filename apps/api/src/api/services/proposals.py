"""Change proposals: an agent stages edits to its instructions or skills; a person decides.

The agent's ``propose_change`` stores a proposal and changes nothing. Safeguards apply before
anything is stored: the agent's ``proposes_changes`` switch, a cap on pending proposals per
agent (``governance.proposal_max_pending``), a cap on the size of one change
(``governance.proposal_max_change_chars``), the credential filter long-term memory uses, and
exact edits that must apply to the current text. A proposal is approved or rejected only by a
person who may edit its target (the agent, or the skill, at its owner: member, team or
organization), and the change is saved under that person's rights. When the target changed
since the proposal, its edits are applied again to the current text, and a conflict is refused.
A skill that follows an external source is not saved: approving opens a pull request on its
repository with the reviewer's linked GitHub account (``source_changes.py``).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select

from api.db.models import ChangeProposalModel, CustomSkillModel
from api.db.session import DatabaseSessionManager
from api.services.audit import AuditService
from api.services.memory import secret_reason
from api.services.ownership import manageable
from api.services.personas import PersonaError, find_agent, save_persona
from api.services.settings import effective_settings
from api.services.skill_sources import link_and_source
from api.services.skill_store import SkillContent, get_skill
from api.services.skills_lookup import _find_visible_skill, _memory_skills
from api.services.source_changes import SkillChangeError, propose_skill_change
from specifications.interfaces.ownership import Viewer
from specifications.interfaces.proposals import (
    PROPOSAL_STATUSES,
    PROPOSAL_TARGETS,
    ChangeProposal,
    ProposalEdit,
    ProposalsProtocol,
    ProposalStoreProtocol,
)

MAX_SUMMARY_CHARS = 300
MAX_RATIONALE_CHARS = 2_000
MAX_LISTED = 100
_PLATFORM = Viewer(user_id="", is_org_admin=True)


class ProposalError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def apply_edits(text: str, edits: list[ProposalEdit]) -> str:
    """``text`` with each exact replacement applied in order; a miss or an ambiguity raises."""
    for number, edit in enumerate(edits, start=1):
        found = text.count(edit.old_text) if edit.old_text else 0
        if found != 1:
            problem = "was not found" if found == 0 else "matches more than once"
            raise ValueError(f"edit {number}: its old_text {problem} in the current text")
        text = text.replace(edit.old_text, edit.new_text, 1)
    return text


# -- storage ---------------------------------------------------------------------------------


class InMemoryProposalStore(ProposalStoreProtocol):
    def __init__(self) -> None:
        self._rows: dict[str, ChangeProposal] = {}

    async def create(self, proposal: ChangeProposal) -> ChangeProposal:
        self._rows[proposal.id] = proposal
        return proposal

    async def get(self, org_id: str, proposal_id: str) -> ChangeProposal | None:
        found = self._rows.get(proposal_id)
        return found if found is not None and found.org_id == org_id else None

    async def list_for_org(
        self, org_id: str, *, status: str | None, agent_id: str | None, limit: int
    ) -> list[ChangeProposal]:
        rows = [
            p
            for p in self._rows.values()
            if p.org_id == org_id
            and (status is None or p.status == status)
            and (agent_id is None or p.agent_id == agent_id)
        ]
        return sorted(rows, key=lambda p: (p.created_at, p.id), reverse=True)[:limit]

    async def decide(
        self,
        org_id: str,
        proposal_id: str,
        *,
        status: str,
        decided_by: str,
        note: str,
        applied_content: str | None,
        change_request_url: str = "",
    ) -> ChangeProposal | None:
        found = await self.get(org_id, proposal_id)
        if found is None or found.status != "pending":
            return None
        found.status, found.decided_by, found.decision_note = status, decided_by, note
        found.decided_at = int(time.time())
        found.change_request_url = change_request_url
        if applied_content is not None:
            found.proposed_content = applied_content
        return found


class PostgresProposalStore(ProposalStoreProtocol):
    def __init__(self, db: DatabaseSessionManager) -> None:
        self._db = db

    async def create(self, proposal: ChangeProposal) -> ChangeProposal:
        async with self._db.session(tenant_org_id=proposal.org_id) as db:
            db.add(ChangeProposalModel(**_columns(proposal)))
        return proposal

    async def get(self, org_id: str, proposal_id: str) -> ChangeProposal | None:
        async with self._db.session(tenant_org_id=org_id) as db:
            row = await db.get(ChangeProposalModel, proposal_id)
            return _from_row(row) if row is not None and row.org_id == org_id else None

    async def list_for_org(
        self, org_id: str, *, status: str | None, agent_id: str | None, limit: int
    ) -> list[ChangeProposal]:
        statement = select(ChangeProposalModel).where(ChangeProposalModel.org_id == org_id)
        if status is not None:
            statement = statement.where(ChangeProposalModel.status == status)
        if agent_id is not None:
            statement = statement.where(ChangeProposalModel.agent_id == agent_id)
        statement = statement.order_by(ChangeProposalModel.created_at.desc()).limit(limit)
        async with self._db.session(tenant_org_id=org_id) as db:
            return [_from_row(row) for row in (await db.execute(statement)).scalars().all()]

    async def decide(
        self,
        org_id: str,
        proposal_id: str,
        *,
        status: str,
        decided_by: str,
        note: str,
        applied_content: str | None,
        change_request_url: str = "",
    ) -> ChangeProposal | None:
        async with self._db.session(tenant_org_id=org_id) as db:
            # Row lock: two reviewers deciding at once must not both apply the change.
            row = (
                await db.execute(
                    select(ChangeProposalModel)
                    .where(ChangeProposalModel.id == proposal_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or row.org_id != org_id or row.status != "pending":
                return None
            row.status, row.decided_by, row.decision_note = status, decided_by, note
            row.decided_at = int(time.time())
            row.change_request_url = change_request_url
            if applied_content is not None:
                row.proposed_content = applied_content
            return _from_row(row)

    async def count_pending(self, org_id: str, agent_id: str) -> int:
        async with self._db.session(tenant_org_id=org_id) as db:
            return int(
                (
                    await db.execute(
                        select(func.count()).where(
                            ChangeProposalModel.agent_id == agent_id,
                            ChangeProposalModel.status == "pending",
                        )
                    )
                ).scalar_one()
            )


def _columns(p: ChangeProposal) -> dict[str, Any]:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "agent_id": p.agent_id,
        "target_kind": p.target_kind,
        "target_id": p.target_id,
        "target_name": p.target_name,
        "summary": p.summary,
        "rationale": p.rationale,
        "base_content": p.base_content,
        "proposed_content": p.proposed_content,
        "edits": [{"old_text": e.old_text, "new_text": e.new_text} for e in p.edits],
        "status": p.status,
        "session_id": p.session_id,
        "requested_by": p.requested_by,
        "created_at": p.created_at,
        "decided_by": p.decided_by,
        "decided_at": p.decided_at,
        "decision_note": p.decision_note,
        "change_request_url": p.change_request_url,
    }


def _from_row(row: ChangeProposalModel) -> ChangeProposal:
    return ChangeProposal(
        id=row.id,
        org_id=row.org_id,
        agent_id=row.agent_id,
        target_kind=row.target_kind,
        target_id=row.target_id,
        target_name=row.target_name,
        summary=row.summary,
        rationale=row.rationale,
        base_content=row.base_content,
        proposed_content=row.proposed_content,
        edits=[ProposalEdit(str(e["old_text"]), str(e["new_text"])) for e in row.edits or []],
        status=row.status,
        session_id=row.session_id,
        requested_by=row.requested_by,
        created_at=row.created_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        change_request_url=row.change_request_url or "",
    )


_in_memory_store = InMemoryProposalStore()


def proposal_store(db_mgr: DatabaseSessionManager | None) -> ProposalStoreProtocol:
    return PostgresProposalStore(db_mgr) if db_mgr is not None else _in_memory_store


async def _pending_count(store: ProposalStoreProtocol, org_id: str, agent_id: str) -> int:
    if isinstance(store, PostgresProposalStore):
        return await store.count_pending(org_id, agent_id)
    return len(await store.list_for_org(org_id, status="pending", agent_id=agent_id, limit=10_000))


# -- targets ---------------------------------------------------------------------------------


async def _skill_record(db_mgr: Any, org_id: str, skill_id: str) -> dict[str, Any] | None:
    if db_mgr is None:
        found = _memory_skills.get(org_id, {}).get(skill_id)
        return dict(found) if found is not None else None
    async with db_mgr.session(tenant_org_id=org_id) as db:
        row = await db.get(CustomSkillModel, skill_id)
        if row is None or row.org_id != org_id:
            return None
        return {
            "id": row.id,
            "name": row.name,
            "body": row.body,
            "runtime": row.runtime,
            "scope": row.scope,
            "user_id": row.user_id,
            "team_id": row.team_id,
        }


async def _save_skill_body(db_mgr: Any, org_id: str, skill_id: str, body: str) -> None:
    now = int(time.time())
    if db_mgr is None:
        record = _memory_skills.get(org_id, {}).get(skill_id)
        if record is not None:
            record["body"], record["updated_at"] = body, now
        return
    async with db_mgr.session(tenant_org_id=org_id) as db:
        row = await db.get(CustomSkillModel, skill_id)
        if row is not None and row.org_id == org_id:
            row.body, row.updated_at = body, now


async def _current_text(db_mgr: Any, org_id: str, proposal: ChangeProposal) -> str | None:
    if proposal.target_kind == "instructions":
        agent = await find_agent(db_mgr, org_id, _PLATFORM, proposal.target_id)
        return agent.system_prompt if agent is not None else None
    record = await _skill_record(db_mgr, org_id, proposal.target_id)
    return str(record["body"]) if record is not None else None


async def may_review(db_mgr: Any, org_id: str, viewer: Viewer, proposal: ChangeProposal) -> bool:
    """Whether ``viewer`` may edit the proposal's target (the agent, or the skill)."""
    if proposal.target_kind == "instructions":
        agent = await find_agent(db_mgr, org_id, viewer, proposal.target_id)
        return agent is not None and manageable(agent, viewer)
    record = await _skill_record(db_mgr, org_id, proposal.target_id)
    return record is not None and manageable(record, viewer)


# -- the agent's side ------------------------------------------------------------------------


class BoundProposals(ProposalsProtocol):
    """``propose_change`` and ``list_my_proposals`` for one turn's agent."""

    def __init__(
        self,
        *,
        db_mgr: Any,
        org_id: str,
        agent_id: str,
        session_id: str | None,
        requested_by: str,
        audit: AuditService,
    ) -> None:
        self._db, self._org, self._agent = db_mgr, org_id, agent_id
        self._session, self._requested_by, self._audit = session_id, requested_by, audit
        self._store = proposal_store(db_mgr)

    async def propose(
        self,
        target_kind: str,
        skill_name: str,
        edits: list[ProposalEdit],
        summary: str,
        rationale: str,
    ) -> str:
        refusal = await self._refusal(target_kind, edits, summary)
        if refusal:
            return f"Not proposed: {refusal}"
        target = await self._target(target_kind, skill_name)
        if isinstance(target, str):
            return f"Not proposed: {target}"
        target_id, target_name, base = target
        try:
            proposed = apply_edits(base, edits)
        except ValueError as err:
            return f"Not proposed: {err}. Quote the current text exactly."
        if proposed == base:
            return "Not proposed: the edits change nothing."
        proposal = await self._store.create(
            ChangeProposal(
                id=f"prop_{uuid.uuid4().hex[:12]}",
                org_id=self._org,
                agent_id=self._agent,
                target_kind=target_kind,
                target_id=target_id,
                target_name=target_name,
                summary=summary.strip()[:MAX_SUMMARY_CHARS],
                rationale=rationale.strip()[:MAX_RATIONALE_CHARS],
                base_content=base,
                proposed_content=proposed,
                edits=list(edits),
                session_id=self._session,
                requested_by=self._requested_by,
            )
        )
        await self._audit.record(
            "proposal.created",
            org_id=self._org,
            session_id=self._session,
            tool_name="propose_change",
            result_summary=f"{proposal.id}: {target_kind} {target_name}",
            metadata={"agent_id": self._agent, "proposal_id": proposal.id},
        )
        return (
            f"Proposal {proposal.id} is waiting for review ({target_kind} '{target_name}'). It "
            "changes nothing until a person who may edit it approves it; until then, keep "
            "working with the current text."
        )

    async def _refusal(self, target_kind: str, edits: list[ProposalEdit], summary: str) -> str:
        governance = await effective_settings(self._db, self._org, "governance")
        if target_kind not in PROPOSAL_TARGETS:
            return f"target must be one of {', '.join(PROPOSAL_TARGETS)}"
        if not edits:
            return "give at least one edit"
        if not summary.strip():
            return "give a one-line summary of the change"
        size = sum(len(e.old_text) + len(e.new_text) for e in edits)
        limit = int(governance.get("proposal_max_change_chars") or 8000)
        if size > limit:
            return f"the change is {size:,} characters, over the {limit:,} allowed; split it"
        if any(secret_reason(e.new_text) for e in edits):
            return "the change contains what looks like a credential, which never goes in"
        pending = await _pending_count(self._store, self._org, self._agent)
        if pending >= int(governance.get("proposal_max_pending") or 5):
            return (
                f"you already have {pending} proposals waiting for review; wait for a decision "
                "or name them to the person"
            )
        return ""

    async def _target(self, target_kind: str, skill_name: str) -> tuple[str, str, str] | str:
        agent = await find_agent(self._db, self._org, _PLATFORM, self._agent)
        if agent is None:
            return "this agent cannot be found"
        if target_kind == "instructions":
            return agent.id, agent.name, agent.system_prompt
        owner = agent.user_id or ""
        skill = await _find_visible_skill(
            self._db,
            self._org,
            owner,
            skill_name.strip(),
            allowed_ids=agent.skill_ids,
            agent_team_id=agent.team_id if agent.scope == "team" else None,
        )
        if skill is None:
            return (
                f"no skill '{skill_name}' of yours can be changed here (a skill a repository "
                "brings changes through a pull request)"
            )
        if skill.id.startswith("starter:") or skill.runtime != "text":
            return f"'{skill_name}' is a built-in or code skill and cannot be changed this way"
        return skill.id, skill_name.strip(), skill.body

    async def list_mine(self, status: str | None, limit: int) -> list[ChangeProposal]:
        wanted = status if status in PROPOSAL_STATUSES else None
        return await self._store.list_for_org(
            self._org, status=wanted, agent_id=self._agent, limit=max(1, min(limit, 20))
        )


# -- the reviewer's side ---------------------------------------------------------------------


async def reviewable(
    db_mgr: Any, org_id: str, viewer: Viewer, status: str | None, limit: int = MAX_LISTED
) -> list[ChangeProposal]:
    """The organization's proposals ``viewer`` may decide, newest first."""
    rows = await proposal_store(db_mgr).list_for_org(
        org_id, status=status, agent_id=None, limit=MAX_LISTED
    )
    return [p for p in rows if await may_review(db_mgr, org_id, viewer, p)][:limit]


async def reviewable_one(
    db_mgr: Any, org_id: str, viewer: Viewer, proposal_id: str
) -> ChangeProposal:
    proposal = await proposal_store(db_mgr).get(org_id, proposal_id)
    if proposal is None or not await may_review(db_mgr, org_id, viewer, proposal):
        raise ProposalError(404, "No such proposal you may review.")
    return proposal


async def approve(
    db_mgr: Any,
    org_id: str,
    viewer: Viewer,
    proposal_id: str,
    *,
    content: str | None,
    note: str,
    audit: AuditService,
    state: Any = None,
) -> ChangeProposal:
    """Apply the proposal (or the reviewer's edited ``content``) under the reviewer's rights;
    for a skill that follows an external source, open the pull request instead (``state`` is
    the application's, for the GitHub access)."""
    proposal = await reviewable_one(db_mgr, org_id, viewer, proposal_id)
    if proposal.status != "pending":
        raise ProposalError(409, f"This proposal is already {proposal.status}.")
    current = await _current_text(db_mgr, org_id, proposal)
    if current is None:
        raise ProposalError(409, "Its agent or skill no longer exists.")
    new_text = content if content is not None else _rebased(proposal, current)
    change_url = await _apply(
        state or SimpleNamespace(db_manager=db_mgr), org_id, viewer, proposal, new_text
    )
    decided = await proposal_store(db_mgr).decide(
        org_id,
        proposal_id,
        status="approved",
        decided_by=viewer.user_id,
        note=note.strip()[:MAX_RATIONALE_CHARS],
        applied_content=new_text,
        change_request_url=change_url,
    )
    if decided is None:
        raise ProposalError(409, "Someone decided this proposal first.")
    await _audit_decision(audit, org_id, viewer, decided, "proposal.approved")
    return decided


def _rebased(proposal: ChangeProposal, current: str) -> str:
    if current == proposal.base_content:
        return proposal.proposed_content
    try:
        return apply_edits(current, proposal.edits)
    except ValueError as err:
        raise ProposalError(
            409,
            f"The {proposal.target_kind} changed since this proposal and its edits no longer "
            f"apply ({err}). Edit the proposed text, or reject it.",
        ) from err


async def _apply(
    state: Any, org_id: str, viewer: Viewer, proposal: ChangeProposal, new_text: str
) -> str:
    """Save the change; the address of the pull request when it is one, else empty."""
    db_mgr = getattr(state, "db_manager", None)
    if proposal.target_kind == "instructions":
        agent = await find_agent(db_mgr, org_id, viewer, proposal.target_id)
        if agent is None:
            raise ProposalError(404, "No such proposal you may review.")
        try:
            await save_persona(
                db_mgr,
                org_id,
                viewer.user_id,
                viewer.is_org_admin,
                agent.model_copy(update={"system_prompt": new_text}),
                viewer=viewer,
            )
        except PersonaError as err:
            raise ProposalError(err.status_code, err.detail) from err
        return ""
    record = await _skill_record(db_mgr, org_id, proposal.target_id)
    if record is None or not manageable(record, viewer):
        raise ProposalError(403, "Only someone who may edit this skill can approve it.")
    if await link_and_source(state, org_id, proposal.target_id) is not None:
        return await _propose_upstream(state, org_id, viewer, proposal, new_text)
    await _save_skill_body(db_mgr, org_id, proposal.target_id, new_text)
    return ""


async def _propose_upstream(
    state: Any, org_id: str, viewer: Viewer, proposal: ChangeProposal, new_text: str
) -> str:
    skill = await get_skill(getattr(state, "db_manager", None), org_id, proposal.target_id)
    if skill is None:
        raise ProposalError(409, "Its agent or skill no longer exists.")
    try:
        change = await propose_skill_change(
            state,
            org_id,
            viewer,
            skill.id,
            SkillContent(skill.name, skill.description, new_text.strip()),
            summary=proposal.summary,
            rationale=proposal.rationale,
        )
    except SkillChangeError as err:
        raise ProposalError(err.status_code, err.detail) from err
    return change.url


async def reject(
    db_mgr: Any, org_id: str, viewer: Viewer, proposal_id: str, *, note: str, audit: AuditService
) -> ChangeProposal:
    await reviewable_one(db_mgr, org_id, viewer, proposal_id)
    decided = await proposal_store(db_mgr).decide(
        org_id,
        proposal_id,
        status="rejected",
        decided_by=viewer.user_id,
        note=note.strip()[:MAX_RATIONALE_CHARS],
        applied_content=None,
    )
    if decided is None:
        raise ProposalError(409, "This proposal is already decided.")
    await _audit_decision(audit, org_id, viewer, decided, "proposal.rejected")
    return decided


async def _audit_decision(
    audit: AuditService, org_id: str, viewer: Viewer, proposal: ChangeProposal, event: str
) -> None:
    await audit.record(
        event,
        org_id=org_id,
        actor_user_id=viewer.user_id,
        session_id=proposal.session_id,
        result_summary=f"{proposal.id}: {proposal.target_kind} {proposal.target_name}",
        metadata={"agent_id": proposal.agent_id, "proposal_id": proposal.id},
    )


def without_content(proposal: ChangeProposal) -> ChangeProposal:
    """A listing row: the texts are fetched one proposal at a time."""
    return replace(proposal, base_content="", proposed_content="")
