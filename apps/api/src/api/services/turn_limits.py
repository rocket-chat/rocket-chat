"""How many reasoning steps a turn may take: the organization's ceiling and what may be set below it."""

from __future__ import annotations

import logging
from typing import Any

from config_engine.environment import env_value

from api.services.settings import resolve_setting
from specifications.interfaces.agent import DEFAULT_MAX_TURNS

logger = logging.getLogger(__name__)


def deployment_max_turns() -> int:
    """The deployment's default step limit: ``AGENT_MAX_TURNS``, else the platform default."""
    env_max = env_value("AGENT_MAX_TURNS", "ROCKET_MAX_TURNS", "MAX_TURNS")
    return int(env_max) if env_max and env_max.isdigit() else DEFAULT_MAX_TURNS


async def organization_max_turns(db_mgr: Any, org_id: str) -> int:
    """The organization's step limit: its ``max_turns`` setting, else the deployment's
    (``AGENT_MAX_TURNS``, the setting's instance default), else the platform default.

    It is both the default for an agent that sets nothing and the ceiling for one that does.
    """
    resolved = await resolve_setting(db_mgr, org_id, "general", "max_turns")
    try:
        return int(resolved.value)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid general.max_turns=%r for org %s", resolved.value, org_id)
        return deployment_max_turns()
