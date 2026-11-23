"""Change proposals: an agent stages edits to its own instructions or to its skills; a person
who may edit that agent or skill approves (as proposed or edited), or rejects them.

Nothing an agent proposes takes effect by itself: the agent's ``propose_change`` only stores a
proposal, and the change is applied by the reviewer's own save, under the reviewer's rights.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

PROPOSAL_TARGETS = ("instructions", "skill")
PROPOSAL_STATUSES = ("pending", "approved", "rejected")


@dataclass(frozen=True)
class ProposalEdit:
    """One exact replacement: ``old_text`` must occur exactly once in the current text."""

    old_text: str
    new_text: str


@dataclass
class ChangeProposal:
    """A staged change to an agent's instructions or to a skill."""

    id: str
    org_id: str
    agent_id: str
    target_kind: str  # "instructions" | "skill"
    target_id: str  # the agent id, or the skill id
    target_name: str
    summary: str
    rationale: str
    # The text the edits were made against, and the text they produce.
    base_content: str
    proposed_content: str
    edits: list[ProposalEdit] = field(default_factory=list)
    status: str = "pending"
    session_id: str | None = None
    # Who the agent was talking to when it proposed (a member id or a Slack mention).
    requested_by: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))
    decided_by: str | None = None
    decided_at: int | None = None
    decision_note: str = ""
    # A skill linked to an external source is changed through its repository: approving opens
    # a pull request (as the reviewer), and this is its address.
    change_request_url: str = ""


class ProposalStoreProtocol(ABC):
    """Durable proposals of an organization."""

    @abstractmethod
    async def create(self, proposal: ChangeProposal) -> ChangeProposal:
        """Store a new pending proposal."""

    @abstractmethod
    async def get(self, org_id: str, proposal_id: str) -> ChangeProposal | None:
        """One proposal, or None."""

    @abstractmethod
    async def list_for_org(
        self, org_id: str, *, status: str | None, agent_id: str | None, limit: int
    ) -> list[ChangeProposal]:
        """The organization's proposals, newest first, optionally of one status or agent."""

    @abstractmethod
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
        """Settle a pending proposal; None when it is missing or already decided."""


class ProposalsProtocol(ABC):
    """The proposal tools' view, bound to one turn's agent, session and requester."""

    @abstractmethod
    async def propose(
        self,
        target_kind: str,
        skill_name: str,
        edits: list[ProposalEdit],
        summary: str,
        rationale: str,
    ) -> str:
        """Stage a proposal; returns what happened, for the agent (never raises on refusal)."""

    @abstractmethod
    async def list_mine(self, status: str | None, limit: int) -> list[ChangeProposal]:
        """This agent's proposals, newest first."""
