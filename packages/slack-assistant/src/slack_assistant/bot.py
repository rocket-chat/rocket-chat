"""One Slack bot: its own Slack app (and ``@handle``), agent or router, response mode and policy.

An organization can run several bots in its workspace; each is a separate Slack app with its own
tokens and connection. Bots are configured in the cockpit and loaded by the API, which builds
these values from the database (the package never reads its storage).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

from slack_assistant.policy import SlackOrgPolicy, _as_list, _profiles

RESPONSE_MODES = ("thread", "private")
DISMISS = "dismiss"
DEFAULT_COMMAND = "/rocket"


def command_for(handle: str) -> str:
    """A bot's slash command, named after its handle (``@triage`` -> ``/triage``).

    Slack does not namespace slash commands: when two apps of a workspace declare the same one,
    only the most recently installed receives it. Each bot therefore has its own.
    """
    name = re.sub(r"[^a-z0-9_-]+", "-", handle.lower()).strip("-")[:31]
    return f"/{name}" if name else DEFAULT_COMMAND


@dataclass(frozen=True)
class SlackRoute:
    """One destination of a router: when to choose it, which agent answers and how."""

    name: str
    description: str
    agent_id: str
    examples: tuple[str, ...] = ()
    response_mode: str = ""  # empty: the bot's
    enabled: bool = True

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> SlackRoute:
        return cls(
            name=str(raw.get("name") or "").strip(),
            description=str(raw.get("description") or "").strip(),
            agent_id=str(raw.get("agent_id") or ""),
            examples=tuple(_as_list(raw.get("examples"))),
            response_mode=str(raw.get("response_mode") or ""),
            enabled=bool(raw.get("enabled", True)),
        )


@dataclass(frozen=True)
class SlackRouter:
    """Classifies each new request to one route, or dismisses it."""

    classifier_agent_id: str
    routes: tuple[SlackRoute, ...]
    allow_dismiss: bool = False
    dismiss_description: str = ""
    # Below this confidence the fallback applies (a route name, or "dismiss").
    min_confidence: float = 0.5
    fallback: str = ""
    # Follow-ups in a thread keep the route chosen for it.
    sticky: bool = True
    context_messages: int = 5
    classifier_model: str = ""

    def active_routes(self) -> tuple[SlackRoute, ...]:
        return tuple(r for r in self.routes if r.enabled and r.name and r.agent_id)

    def route(self, name: str) -> SlackRoute | None:
        return next((r for r in self.active_routes() if r.name == name), None)

    def choices(self) -> tuple[str, ...]:
        """What the classifier may answer: the active routes, and dismiss when allowed."""
        names = tuple(r.name for r in self.active_routes())
        return (*names, DISMISS) if self.allow_dismiss else names

    def resolve(self, decision: RouteDecision | None) -> str:
        """The route a decision leads to: a route name, ``DISMISS``, or ``""`` for the bot's
        own agent. Unknown or unsure decisions take the fallback."""
        if decision is not None and decision.confidence >= self.min_confidence:
            if decision.route == DISMISS and self.allow_dismiss:
                return DISMISS
            if self.route(decision.route) is not None:
                return decision.route
        if self.fallback == DISMISS:
            return DISMISS
        return self.fallback if self.route(self.fallback) is not None else ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> SlackRouter | None:
        if not raw or not raw.get("classifier_agent_id"):
            return None
        routes = tuple(
            SlackRoute.from_mapping(r) for r in raw.get("routes") or [] if isinstance(r, Mapping)
        )
        return cls(
            classifier_agent_id=str(raw["classifier_agent_id"]),
            routes=routes,
            allow_dismiss=bool(raw.get("allow_dismiss", False)),
            dismiss_description=str(raw.get("dismiss_description") or ""),
            min_confidence=float(raw.get("min_confidence", 0.5)),
            fallback=str(raw.get("fallback") or ""),
            sticky=bool(raw.get("sticky", True)),
            context_messages=int(raw.get("context_messages", 5)),
            classifier_model=str(raw.get("classifier_model") or ""),
        )


@dataclass(frozen=True)
class RouteDecision:
    """What the classifier chose for a message."""

    route: str  # a route name, or DISMISS
    confidence: float
    reason: str
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ClassifyRequest:
    """A message to route, with what the classifier may see of its thread."""

    bot: SlackBot
    org_id: str
    text: str
    thread_excerpt: str
    channel_id: str
    thread_ts: str
    slack_user_id: str


@dataclass(frozen=True)
class SlackBot:
    """Everything one bot needs to connect and behave."""

    id: str
    org_id: str
    name: str
    bot_token: str
    app_token: str
    agent_id: str = ""
    router: SlackRouter | None = None
    response_mode: str = "thread"
    respond_in_dms: bool = True
    # Channels where the bot also reads messages that do not mention it (routing on each one).
    listen_channels: frozenset[str] = frozenset()
    # Private mode: the reaction marking a request taken, and whether the result is also sent
    # as a direct message (the ephemeral reply disappears when Slack is reloaded).
    private_reaction: str = "white_check_mark"
    private_dm_result: bool = False
    # Private mode: questions, approvals, errors and the result go to the requester's DMs
    # (with a link to the thread) instead of ephemeral messages a reload makes disappear.
    private_dm_updates: bool = False
    # Overrides of the organization's Slack policy (the ``slack`` settings), key by key.
    policy: Mapping[str, Any] = field(default_factory=dict)
    command: str = DEFAULT_COMMAND

    @property
    def session_key(self) -> str:
        """Part of every session id of this bot: two bots in one thread get two sessions.
        Short, as a private session id also carries the channel, thread and requester."""
        return self.id.replace("-", "").replace("_", "")[:8]

    def with_agent(self, policy: SlackOrgPolicy) -> SlackOrgPolicy:
        """A policy already resolved with this bot's overrides, given the bot's agent."""
        return replace(policy, default_agent_persona=self.agent_id or policy.default_agent_persona)

    def apply_to(self, org_policy: SlackOrgPolicy) -> SlackOrgPolicy:
        """The organization's policy with this bot's overrides, agent and channel overrides,
        merged here when no policy resolver (which also honours locks) is wired."""
        overrides = {k: v for k, v in self.policy.items() if v not in (None, "", [], {})}
        merged = SlackOrgPolicy.from_settings({**_policy_as_settings(org_policy), **overrides})
        return replace(
            merged,
            # Not a setting (the bot's agent, or its routing decision): kept from the policy.
            default_agent_persona=self.agent_id or org_policy.default_agent_persona,
            channel_profiles=_profiles(overrides.get("channel_profiles"))
            or org_policy.channel_profiles,
        )


def _policy_as_settings(policy: SlackOrgPolicy) -> dict[str, Any]:
    """A policy back as ``slack`` settings, so bot overrides merge key by key.

    Built from the dataclass's own fields: a hand-written list silently dropped any field added
    later (``restrict_linked_members`` was reset to off for every bot).
    """
    settings: dict[str, Any] = {}
    for item in fields(policy):
        value = getattr(policy, item.name)
        if item.name == "channel_profiles":
            value = {channel: asdict(profile) for channel, profile in value.items()}
        elif isinstance(value, frozenset | set):
            value = sorted(value)
        elif isinstance(value, tuple):
            value = list(value)
        settings[item.name] = value
    return settings
