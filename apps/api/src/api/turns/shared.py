"""Process-wide collaborators of the turn engine."""

import asyncio
import logging
import os
from typing import Any

from config_engine import CascadingSettingsResolver
from pydantic import BaseModel

from api.services.audit import (
    GovernanceRateLimiter,
)
from api.services.turn_service import TurnService
from api.turns.routing import (
    _resolve_effective_max_turns,
    _resolve_turn_routing,
    resolve_agent_persona,
)

logger = logging.getLogger(__name__)


_background_tasks: set[asyncio.Task[Any]] = set()

_settings_resolver = CascadingSettingsResolver()


def _api_rate_limit_setting(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) >= 1 else default


# Authenticated-path rate limits: the Slack bot is bounded per
# workspace and per (team, user); the API turn path needs the same ceiling. The buckets
# are process-wide and bounded, matching the shipped replicas: 1 topology.
_api_rate_limiter = GovernanceRateLimiter(
    per_user_per_minute=_api_rate_limit_setting("ROCKET_API_RATE_LIMIT_PER_USER", 60),
    per_org_per_minute=_api_rate_limit_setting("ROCKET_API_RATE_LIMIT_PER_ORG", 300),
)


class FollowUpSuggestion(BaseModel):
    """Validated shape of the auxiliary "what next?" suggestion."""

    suggestion: str


# Shared turn plan/invocation service: both the web handler above and the Slack
# assistant use this, so agent/model/turn/toolset resolution cannot drift between them.
_turn_service = TurnService(
    resolve_agent=resolve_agent_persona,
    resolve_routing=_resolve_turn_routing,
    resolve_max_turns=_resolve_effective_max_turns,
)
