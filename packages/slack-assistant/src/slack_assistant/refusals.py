"""What Slack says when the platform refused a turn: a spend limit, or an agent not the
person's to use. Neither is a malfunction, so neither reads as one."""

from __future__ import annotations

from collections.abc import Mapping


def budget_blocked_text(payload: Mapping[str, object]) -> str:
    """A ``budget_blocked`` event as a Slack message: which limit, and when it resets."""
    message = str(payload.get("message") or "A spend limit stopped this request.")
    return f":no_entry: *Spend limit reached.* {message}"


def access_denied_text(payload: Mapping[str, object]) -> str:
    """An ``agent_access_denied`` event as a Slack message: what to do instead."""
    name = str(payload.get("agent_name") or payload.get("agent_id") or "this agent")
    return (
        f":lock: *You don't have access to {name}.* Ask an administrator for access, or "
        "continue this conversation in Rocket Chat with another agent."
    )
