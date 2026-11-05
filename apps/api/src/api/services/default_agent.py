"""A member's default agent: the agent their sessions start with when none is chosen.

It is the ``user_preferences.default_agent`` setting, an organization default, resolved by the
one settings resolver with two rules of its own:

* the team level counts only for a member of exactly one team: with several there is no way
  to say whose default wins, and the session itself has no team until its agent gives it one;
* a level whose agent the member may not use (deleted, disabled, moved to another owner, not
  granted) is skipped, never an error: a stale default must not stop a session from starting.

Used by the cockpit's new-session picker (``GET /v1/me/default-agent``), by a session created
through the API without an agent, and by a Slack direct message to a bot without an agent.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from config_engine import FieldError
from config_engine.registry import Writer

from api.services.agent_access import AgentAccessDenied
from api.services.personas import get_persona
from api.services.settings import (
    load_org_config,
    load_team_config,
    load_user_config,
    resolve_setting,
)
from api.services.teams import teams_of
from specifications.interfaces.agent import DefaultAgentChoice

DEFAULT_AGENT_DOMAIN = "user_preferences"
DEFAULT_AGENT_KEY = "default_agent"


async def sole_team(db_mgr: Any, org_id: str, user_id: str) -> str | None:
    """The member's team when they belong to exactly one, else ``None``."""
    teams = await teams_of(db_mgr, org_id, user_id)
    return next(iter(teams)) if len(teams) == 1 else None


async def _usable(
    db_mgr: Any, org_id: str, user_id: str, roles: list[str] | None, agent_id: str
) -> bool:
    try:
        return await get_persona(db_mgr, org_id, user_id, agent_id, roles=roles) is not None
    except AgentAccessDenied:
        return False


def _stored(config: Mapping[str, Any]) -> str:
    value = config.get(DEFAULT_AGENT_KEY)
    return value.strip() if isinstance(value, str) else ""


async def _candidates(
    db_mgr: Any, org_id: str, user_id: str | None, team_id: str | None
) -> set[str]:
    """The agents the levels name, to test each once before the resolver chooses."""
    org, _ = await load_org_config(db_mgr, org_id, DEFAULT_AGENT_DOMAIN)
    team = await load_team_config(db_mgr, org_id, team_id, DEFAULT_AGENT_DOMAIN) if team_id else {}
    own = await load_user_config(db_mgr, org_id, user_id, DEFAULT_AGENT_DOMAIN) if user_id else {}
    return {value for value in (_stored(org), _stored(team), _stored(own)) if value}


async def resolve_default_agent(
    db_mgr: Any,
    org_id: str,
    user_id: str,
    roles: list[str] | None = None,
    *,
    include_member: bool = True,
) -> DefaultAgentChoice:
    """The member's default agent and the level it comes from.

    ``include_member=False`` answers what the member inherits: the same resolution without
    their own choice (the settings page shows it beside the "follow" option).
    """
    team_id = await sole_team(db_mgr, org_id, user_id)
    member = user_id if include_member else None
    usable = {
        agent_id
        for agent_id in await _candidates(db_mgr, org_id, member, team_id)
        if await _usable(db_mgr, org_id, user_id, roles, agent_id)
    }
    resolved = await resolve_setting(
        db_mgr,
        org_id,
        DEFAULT_AGENT_DOMAIN,
        DEFAULT_AGENT_KEY,
        user_id=member,
        team_id=team_id,
        accept=lambda value: not value or str(value).strip() in usable,
    )
    locked = resolved.locked_by == "org"
    # Under a lock the resolver reports the lower levels as refused: they are overruled, not
    # skipped. It also returns a locked value without the acceptance test, so it is made here.
    skipped = () if locked else resolved.refused
    value = str(resolved.value or "").strip()
    if value and value not in usable:
        return DefaultAgentChoice(None, "platform", (*skipped, resolved.source), team_id, locked)
    if not value:
        return DefaultAgentChoice(None, "platform", skipped, team_id, locked)
    return DefaultAgentChoice(value, resolved.source, skipped, team_id, locked)


async def default_agent_errors(
    db_mgr: Any,
    org_id: str,
    user_id: str,
    roles: list[str] | None,
    *,
    domain: str,
    writer: Writer,
    values: Mapping[str, Any],
    stored: Mapping[str, Any],
) -> list[FieldError]:
    """Why a default agent being saved is refused: one the writer cannot see, a personal agent
    as an organization's or team's default, or (for a member) one they may not use.

    A value already stored is not checked again: an agent removed since is skipped when the
    default is resolved, and must not block saving the other settings of the form.
    """
    if domain != DEFAULT_AGENT_DOMAIN:
        return []
    agent_id = _stored(values)
    if not agent_id or agent_id == _stored(stored):
        return []
    try:
        agent = await get_persona(
            db_mgr, org_id, user_id, agent_id, roles=roles, enforce_access=writer == "member"
        )
    except AgentAccessDenied:
        return [FieldError(DEFAULT_AGENT_KEY, "you may not use this agent")]
    if agent is None:
        return [FieldError(DEFAULT_AGENT_KEY, f"no agent '{agent_id}' you can see")]
    if writer != "member" and (agent.scope == "user" or agent.user_id):
        return [
            FieldError(
                DEFAULT_AGENT_KEY,
                "a personal agent cannot be the default of an organization or a team",
            )
        ]
    return []
