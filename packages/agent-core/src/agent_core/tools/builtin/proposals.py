"""Proposing changes to the agent's own instructions and skills (``proposes_changes`` agents)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, Field

from agent_core.context import current_execution_ctx
from specifications.interfaces.proposals import ProposalEdit, ProposalsProtocol

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

NOT_ENABLED = (
    "Error: this agent may not propose changes (an administrator turns this on in the agent's "
    "settings)."
)


class Edit(BaseModel):
    """One exact replacement in the current text."""

    old_text: str = Field(description="Current text to replace; it must occur exactly once.")
    new_text: str = Field(description="What it becomes (empty deletes it).")


def _proposals() -> ProposalsProtocol | None:
    ctx = current_execution_ctx.get()
    if ctx is None or not ctx.policy.proposes_changes:
        return None
    return ctx.callbacks.proposals


def register(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Propose a change to your own instructions or to one of your skills when a person "
            "taught you something lasting (a correction, a rule, a better method). It changes "
            "nothing by itself: a person who may edit that agent or skill approves, edits or "
            "rejects it. Give exact replacements of the current text, a one-line summary and "
            "why (who said what). Never put credentials or personal data in it."
        )
    )
    async def propose_change(
        target: Annotated[
            Literal["instructions", "skill"],
            Field(description="Your instructions (system prompt), or one of your skills."),
        ],
        edits: Annotated[list[Edit], Field(description="Exact replacements, applied in order.")],
        summary: Annotated[str, Field(description="One line: what changes.")],
        rationale: Annotated[
            str, Field(description="Why: what was said, by whom, in which conversation.")
        ],
        skill_name: Annotated[
            str, Field(description="The skill's name, when target is 'skill'.")
        ] = "",
    ) -> str:
        proposals = _proposals()
        if proposals is None:
            return NOT_ENABLED
        return await proposals.propose(
            target,
            skill_name,
            [ProposalEdit(e.old_text, e.new_text) for e in edits],
            summary,
            rationale,
        )

    @registry._tool(
        description=(
            "List your change proposals and their decisions (pending, approved, rejected), "
            "newest first, so you neither propose twice nor ignore a rejection's reason."
        )
    )
    async def list_my_proposals(
        status: Annotated[
            Literal["pending", "approved", "rejected", "all"],
            Field(description="Which proposals (default: pending)."),
        ] = "pending",
    ) -> str:
        proposals = _proposals()
        if proposals is None:
            return NOT_ENABLED
        found = await proposals.list_mine(None if status == "all" else status, 20)
        if not found:
            return "No proposals."
        lines = []
        for p in found:
            when = dt.datetime.fromtimestamp(p.created_at, tz=dt.UTC).strftime("%Y-%m-%d")
            note = f" — {p.decision_note[:200]}" if p.decision_note else ""
            if p.change_request_url:
                note += f" — pull request {p.change_request_url} (applies once merged)"
            lines.append(
                f"- {p.id} · {p.status} · {p.target_kind} '{p.target_name}' · {when} · "
                f"{p.summary}{note}"
            )
        return "\n".join(lines)
