"""How many reasoning steps a turn may take: the organization's ceiling and what may be set below it."""

from __future__ import annotations

import logging
from typing import Any

from config_engine.environment import env_value

from api.services.settings import load_org_config
from specifications.interfaces.agent import DEFAULT_MAX_TURNS

logger = logging.getLogger(__name__)


def deployment_max_turns() -> int:
    """The deployment's default step limit: ``AGENT_MAX_TURNS``, else the platform default."""
    env_max = env_value("AGENT_MAX_TURNS", "ROCKET_MAX_TURNS", "MAX_TURNS")
    return int(env_max) if env_max and env_max.isdigit() else DEFAULT_MAX_TURNS


async def organization_max_turns(db_mgr: Any, org_id: str) -> int:
    """The organization's step limit: its ``max_turns`` setting, else the deployment default.

    It is both the default for an agent that sets nothing and the ceiling for one that does.
    """
    org_general, _ = await load_org_config(db_mgr, org_id, "general")
    configured = org_general.get("max_turns")
    if configured is not None:
        try:
            return int(configured)
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid general.max_turns=%r for org %s", configured, org_id)
    return deployment_max_turns()
