"""Security context injected into API request lifecycle from validated OIDC/SSO tokens."""

from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class RequestContext:
    """Security and tenant identity context extracted from authenticated JWT claims."""

    user_id: str
    org_id: str
    email: str
    team_id: str | None = None
    name: str | None = None
    roles: list[str] = field(default_factory=lambda: ["member"])
    groups: list[str] = field(default_factory=list)
    # How the identity was established ("session" cookie/token, "oidc", "header", "dev").
    source: str = "unknown"


# The caller's teams in their organization, bound once per request (and per socket) beside the
# tenant, as (user id, teams): sessions shared with a team open to its members through the one
# access check, which has no database of its own.
current_caller_teams: ContextVar[tuple[str, frozenset[str]] | None] = ContextVar(
    "current_caller_teams", default=None
)


def caller_teams(user_id: str) -> frozenset[str]:
    """The teams bound for ``user_id`` in this request; none for anybody else."""
    bound = current_caller_teams.get()
    return bound[1] if bound is not None and bound[0] == user_id else frozenset()
