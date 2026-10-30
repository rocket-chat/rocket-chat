"""Per-workspace quota enforcement for the unauthenticated Slack path.

``slack_workspace_quotas`` (migration ``007``) holds one row per Slack workspace with the
configured caps (``turns_per_hour``, ``max_concurrent_sessions``, plus the organization's
``slack_workspace`` spend limits) and
the mutable counters the pre-dispatch check reads. This service turns that row into an
admission decision and consumes the hourly budget atomically, so two concurrent turns
cannot both slip past the last turn of the window.

The service implements :class:`~slack_assistant.guardrails.WorkspaceQuotaGate`, letting the
Slack package depend only on the contract, never on the API application (layering rule).
When no database is configured, an in-process store keeps development and tests working;
a missing row means "no cap configured" (the schema stores ``0`` as the default, which
would otherwise make the bot unusable out of the box).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from slack_assistant.guardrails import QuotaDecision
from sqlalchemy import select

from api.db.models import SlackWorkspaceQuotaModel
from api.db.session import DatabaseSessionManager

logger = logging.getLogger(__name__)

__all__ = [
    "InMemoryWorkspaceQuotaStore",
    "PostgresWorkspaceQuotaStore",
    "SlackWorkspaceQuotaService",
]

_HOUR_SECONDS = 3600


@dataclass
class WorkspaceQuotaState:
    """The caps and counters of one workspace, decoupled from the ORM entity."""

    turns_per_hour: int = 0
    max_concurrent_sessions: int = 0
    # A ``slack_workspace`` spend limit of the organization is used up (``spend_limits``).
    over_spend_limit: bool = False
    turns_this_hour: int = 0
    hour_window_start: float | None = None


def _budget_message() -> str:
    return (
        "🚫 *Workspace budget reached.* This Slack workspace has used its spend limit. "
        "An administrator can raise it under Settings > Organization > Spend."
    )


def _quota_message(cap: int, retry_after_seconds: int) -> str:
    return (
        f"🚫 *Workspace quota exceeded.* This workspace allows {cap} turn(s) per hour and "
        f"the limit is currently reached. Please try again in about "
        f"{max(1, retry_after_seconds)} seconds."
    )


def _concurrency_message(cap: int) -> str:
    return (
        f"🚦 *Too many concurrent requests.* This workspace allows {cap} in-flight "
        "turn(s); please wait for one to finish before retrying."
    )


def _decide(state: WorkspaceQuotaState, *, active_sessions: int, now: float) -> QuotaDecision:
    """Apply the configured caps to a workspace state without mutating it."""
    if state.max_concurrent_sessions and active_sessions >= state.max_concurrent_sessions:
        return QuotaDecision(
            False, "concurrency", _concurrency_message(state.max_concurrent_sessions)
        )
    if state.over_spend_limit:
        return QuotaDecision(False, "budget", _budget_message())
    if state.turns_per_hour:
        window_expired = (
            state.hour_window_start is None or now - state.hour_window_start >= _HOUR_SECONDS
        )
        if not window_expired and state.turns_this_hour >= state.turns_per_hour:
            retry_after = max(1, int((state.hour_window_start or now) + _HOUR_SECONDS - now))
            return QuotaDecision(
                False,
                "turns_per_hour",
                _quota_message(state.turns_per_hour, retry_after),
                retry_after_seconds=retry_after,
            )
    return QuotaDecision(True)


def _consume_turn(state: WorkspaceQuotaState, now: float) -> None:
    """Reset the rolling window when needed and count the admitted turn."""
    if not state.turns_per_hour:
        return
    if state.hour_window_start is None or now - state.hour_window_start >= _HOUR_SECONDS:
        state.hour_window_start = now
        state.turns_this_hour = 0
    state.turns_this_hour += 1


SpendProvider = Callable[[str, str], Awaitable[bool]]
"""Whether a ``slack_workspace`` spend limit of (organization, Slack team) is used up."""


class InMemoryWorkspaceQuotaStore:
    """In-process workspace store used when no database is configured (dev + tests)."""

    def __init__(
        self, default: WorkspaceQuotaState | None = None, spend: SpendProvider | None = None
    ) -> None:
        self._default = default or WorkspaceQuotaState()
        self._states: dict[str, WorkspaceQuotaState] = {}
        self._spend = spend

    def set_state(self, team_id: str, state: WorkspaceQuotaState) -> None:
        """Configure a workspace's caps (used by tests and local development)."""
        self._states[team_id] = state

    async def admit_turn(
        self, team_id: str, org_id: str, now: float, active_sessions: int
    ) -> QuotaDecision:
        state = self._states.get(team_id, self._default)
        if self._spend is not None:
            state.over_spend_limit = await self._spend(org_id, team_id)
        decision = _decide(state, active_sessions=active_sessions, now=now)
        if decision.allowed:
            _consume_turn(state, now)
        return decision


class PostgresWorkspaceQuotaStore:
    """Database-backed store; the hourly counter is read-modify-written under a row lock."""

    def __init__(self, manager: DatabaseSessionManager, spend: SpendProvider | None = None) -> None:
        self.manager = manager
        self._spend = spend

    async def admit_turn(
        self, team_id: str, org_id: str, now: float, active_sessions: int
    ) -> QuotaDecision:
        async with self.manager.session(tenant_org_id=org_id) as session:
            row = (
                await session.execute(
                    select(SlackWorkspaceQuotaModel)
                    .where(SlackWorkspaceQuotaModel.team_id == team_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                return QuotaDecision(True)
            state = WorkspaceQuotaState(
                turns_per_hour=row.turns_per_hour,
                max_concurrent_sessions=row.max_concurrent_sessions,
                turns_this_hour=row.turns_this_hour,
                hour_window_start=(
                    float(row.hour_window_start) if row.hour_window_start is not None else None
                ),
            )
            if self._spend is not None:
                state.over_spend_limit = await self._spend(org_id, team_id)
            decision = _decide(state, active_sessions=active_sessions, now=now)
            if decision.allowed:
                _consume_turn(state, now)
                row.turns_this_hour = state.turns_this_hour
                row.hour_window_start = (
                    int(state.hour_window_start) if state.hour_window_start is not None else None
                )
                row.updated_at = int(now)
            return decision


class SlackWorkspaceQuotaService:
    """Admission gate consulted by the Slack assistant *before* any orchestrator call."""

    def __init__(
        self,
        *,
        db_manager: DatabaseSessionManager | None = None,
        clock: Callable[[], float] | None = None,
        default_state: WorkspaceQuotaState | None = None,
        in_memory_store: InMemoryWorkspaceQuotaStore | None = None,
        spend: SpendProvider | None = None,
    ) -> None:
        self._clock = clock or time.time
        if in_memory_store is not None:
            self._store: InMemoryWorkspaceQuotaStore | PostgresWorkspaceQuotaStore = in_memory_store
        elif db_manager is not None:
            self._store = PostgresWorkspaceQuotaStore(db_manager, spend)
        else:
            self._store = InMemoryWorkspaceQuotaStore(default_state, spend)
        self._counters: dict[str, int] = {
            "quota_denied": 0,
            "turns_admitted": 0,
        }

    async def evaluate(
        self,
        *,
        team_id: str,
        org_id: str,
        user_id: str | None,
        channel_id: str,
        active_sessions: int,
        now: float | None = None,
    ) -> QuotaDecision:
        if not team_id:
            # Without a workspace identity there is no quota row to read; the in-process
            # rate limiter in the assistant still bounds the caller.
            return QuotaDecision(True)
        decision = await self._store.admit_turn(
            team_id, org_id, now if now is not None else self._clock(), active_sessions
        )
        if decision.allowed:
            self._counters["turns_admitted"] += 1
        else:
            self._counters["quota_denied"] += 1
        return decision

    def counters(self) -> dict[str, int]:
        return dict(self._counters)
