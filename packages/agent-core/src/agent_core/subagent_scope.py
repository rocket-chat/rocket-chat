"""What a turn shares with its subagents: the money spent, and who a child is.

A child runs on a copy of the turn's execution context (``dataclasses.replace``), so anything
the parent must see change has to be an object both copies point to: ``TurnSpend`` is that
object for cost. ``SubagentFrame`` marks a context as a child's, so the gates it raises carry
its identity and are refused when the role may not ask the person.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field


@dataclass
class TurnSpend:
    """Dollars the turn's model calls cost: the agent's own steps and every child's."""

    parent_usd: float = 0.0
    subagents_usd: float = 0.0
    # Child cost the parent has not folded into its own running total yet.
    unreported_usd: float = 0.0
    by_subagent: dict[str, float] = field(default_factory=dict)

    @property
    def total_usd(self) -> float:
        return self.parent_usd + self.subagents_usd

    def add_subagent(self, subagent_id: str, cost_usd: float) -> None:
        if cost_usd <= 0:
            return
        self.subagents_usd += cost_usd
        self.unreported_usd += cost_usd
        self.by_subagent[subagent_id] = self.by_subagent.get(subagent_id, 0.0) + cost_usd

    def take_unreported(self) -> float:
        """Child cost since the last call, for the parent's running total."""
        taken, self.unreported_usd = self.unreported_usd, 0.0
        return taken


@dataclass(frozen=True)
class SubagentFrame:
    """The child a context belongs to."""

    subagent_id: str
    role: str
    can_ask_user: bool
    parent_tool_call_id: str | None = None


# The id of the tool call being executed, set by the dispatcher around each call: a delegation
# reads it so the child's steps nest under the call that started it.
current_tool_call_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_tool_call_id", default=None
)


def gate_identity(frame: SubagentFrame | None) -> dict[str, str]:
    """The keys a child's approval or question carries, so the card says who is asking."""
    if frame is None:
        return {}
    return {"subagent_id": frame.subagent_id, "subagent_role": frame.role}
