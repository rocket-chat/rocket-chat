"""Security context injected into API request lifecycle from validated OIDC/SSO tokens."""

from dataclasses import dataclass, field


@dataclass
class RequestContext:
    """Security and tenant identity context extracted from authenticated JWT claims."""

    user_id: str
    org_id: str
    email: str
    team_id: str | None = None
    name: str | None = None
    roles: list[str] = field(default_factory=lambda: ["developer"])
    groups: list[str] = field(default_factory=list)
