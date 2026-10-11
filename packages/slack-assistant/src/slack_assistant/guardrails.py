"""Abuse guardrails and enforcement contracts for unauthenticated Slack.

The bot is reachable by anyone in the workspace, so the model must never be trusted to
police itself: the tool allowlist, the destructive-tool blocklist and the consecutive
failure stop are all resolved **server-side** from settings and handed to the execution
layer as data.

This module holds only the decision logic and its contracts — it deliberately has no
dependency on the API application, so the Slack package stays a pure library and the
API's quota service can implement :class:`WorkspaceQuotaGate` against it.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Protocol

__all__ = [
    "DEFAULT_BLOCKED_TOOLS",
    "FailureTracker",
    "QuotaDecision",
    "WorkspaceQuotaGate",
    "parse_tool_csv",
    "resolve_allowed_tools",
    "tool_enforcement_message",
]

# Tools that mutate the workspace or reach external systems destructively. They are
# blocked on the unauthenticated Slack path by default; an operator can tighten (or, if
# they accept the risk, relax) the list via ``SLACK_BLOCKED_TOOLS``.
DEFAULT_BLOCKED_TOOLS: tuple[str, ...] = ("git_revert", "git_open_pr")


def parse_tool_csv(raw: str | None) -> frozenset[str]:
    """Parse a comma-separated tool list; blank entries are ignored."""
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def resolve_allowed_tools(
    *,
    registered: list[str],
    allowed_raw: str | None,
    blocked_raw: str | None,
) -> tuple[list[str] | None, frozenset[str]]:
    """Resolve the effective allowlist for the Slack path.

    Returns ``(whitelisted_tools, blocked_tools)``. ``whitelisted_tools`` is an explicit
    list (never the ``"*"`` wildcard) whenever the registry catalog is known, so the
    execution layer can enforce it — a wildcard would re-open every tool, including the
    blocklisted ones. When the catalog is unknown it returns ``None`` (advisory filter
    only) while still reporting the blocklist.
    """
    allowed = parse_tool_csv(allowed_raw) or frozenset({"*"})
    blocked = parse_tool_csv(blocked_raw) or frozenset(DEFAULT_BLOCKED_TOOLS)
    if not registered:
        return None, blocked
    wildcard = "*" in allowed
    selected = [
        name for name in registered if name not in blocked and (wildcard or name in allowed)
    ]
    return selected, blocked


def tool_enforcement_message(name: str) -> str:
    """The message the server returns when the model requests a blocked tool."""
    return (
        f"Blocked: the tool '{name}' is not permitted in this Slack workspace. "
        "The workspace administrator restricted it for unauthenticated callers; "
        "ask them to allow it, or use the web cockpit where the full toolset is available."
    )


@dataclass(frozen=True)
class QuotaDecision:
    """A pre-dispatch admission decision from the per-workspace quota gate."""

    allowed: bool
    reason: str = ""
    message: str = ""
    retry_after_seconds: int = 0


class WorkspaceQuotaGate(Protocol):
    """The per-workspace quota contract the Slack assistant consults before dispatch."""

    async def evaluate(
        self,
        *,
        team_id: str,
        org_id: str,
        user_id: str | None,
        channel_id: str,
        active_sessions: int,
        now: float | None = None,
    ) -> QuotaDecision: ...

    def counters(self) -> dict[str, int]: ...


@dataclass
class _FailureStreak:
    consecutive_failures: int = 0
    blocked_until: float = 0.0


@dataclass
class FailureTracker:
    """Per-scope consecutive-failure circuit breaker.

    After ``threshold`` failures in a row a scope is refused for ``cooldown_seconds``;
    the window then resets so a transient outage does not permanently mute a workspace.
    The scope map is an LRU with a hard ceiling, so it cannot grow without bound.
    """

    threshold: int
    cooldown_seconds: int
    max_entries: int = 1024
    _streaks: OrderedDict[str, _FailureStreak] = field(default_factory=OrderedDict)

    def _streak(self, key: str) -> _FailureStreak:
        streak = self._streaks.get(key)
        if streak is None:
            streak = _FailureStreak()
            self._streaks[key] = streak
            if len(self._streaks) > self.max_entries:
                self._streaks.popitem(last=False)
        else:
            self._streaks.move_to_end(key)
        return streak

    def is_blocked(self, key: str, now: float) -> bool:
        """Whether ``key`` has tripped the breaker and is still in its cooldown."""
        if self.threshold <= 0:
            return False
        streak = self._streak(key)
        if streak.blocked_until and now < streak.blocked_until:
            return True
        if streak.blocked_until and now >= streak.blocked_until:
            # Cooldown elapsed: give the scope a clean slate and let one request through.
            streak.blocked_until = 0.0
            streak.consecutive_failures = 0
        return False

    def retry_after_seconds(self, key: str, now: float) -> int:
        """Seconds remaining in the cooldown for ``key`` (``0`` when not blocked)."""
        streak = self._streak(key)
        return max(0, int(streak.blocked_until - now))

    def record_failure(self, key: str, now: float) -> None:
        """Record a failed turn; trip the breaker once the threshold is reached."""
        if self.threshold <= 0:
            return
        streak = self._streak(key)
        streak.consecutive_failures += 1
        if streak.consecutive_failures >= self.threshold:
            streak.blocked_until = now + self.cooldown_seconds

    def record_success(self, key: str) -> None:
        """Reset the streak after a completed turn."""
        streak = self._streak(key)
        streak.consecutive_failures = 0
        streak.blocked_until = 0.0
