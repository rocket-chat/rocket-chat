"""Tools that exist for a turn only when its agent's opt-in switch is on."""

from __future__ import annotations

from specifications.interfaces.agent import TurnPolicy

AGENT_SESSION_TOOLS = frozenset({"agent_sessions_list", "agent_session_read"})
PROPOSAL_TOOLS = frozenset({"propose_change", "list_my_proposals"})


def switched_on_tools(policy: TurnPolicy) -> frozenset[str]:
    """The opt-in tools the agent's switches turn on."""
    on: set[str] = set()
    if policy.reads_agent_sessions:
        on |= AGENT_SESSION_TOOLS
    if policy.proposes_changes:
        on |= PROPOSAL_TOOLS
    return frozenset(on)


def switched_off_tools(policy: TurnPolicy) -> frozenset[str]:
    """The opt-in tools the agent's switches leave out of the turn."""
    return (AGENT_SESSION_TOOLS | PROPOSAL_TOOLS) - switched_on_tools(policy)
