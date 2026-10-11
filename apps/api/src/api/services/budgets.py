"""Spending caps enforced before a model call is made.

The organization sets monthly caps for itself and for each member, and a per-session cap;
``0`` means no cap. The check runs inside the LLM gateway - the one place every model call
passes - so a turn that is over budget makes zero provider calls and the user is told which
cap was reached. Spend comes from the persisted ledger, so it is the same on every replica
and survives restarts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from api.services.settings import effective_settings
from api.services.usage import UsageLedger, month_start
from specifications.interfaces.llm import BudgetExceededError, ModelScope

# Caps change rarely and are read before every model call; a short cache keeps that off the
# database. A replica notices an edit within this window.
SETTINGS_TTL_SECONDS = 15.0


@dataclass(frozen=True)
class Cap:
    label: str
    limit_usd: float
    spent_usd: float

    @property
    def active(self) -> bool:
        return self.limit_usd > 0

    @property
    def percent(self) -> float:
        return (self.spent_usd / self.limit_usd * 100) if self.active else 0.0


@dataclass
class BudgetStatus:
    caps: list[Cap] = field(default_factory=list)
    warn_at_percent: int = 80

    def exceeded(self) -> Cap | None:
        return next((c for c in self.caps if c.active and c.spent_usd >= c.limit_usd), None)

    def warnings(self) -> list[str]:
        return [
            f"{cap.label} budget is {cap.percent:.0f}% used "
            f"(${cap.spent_usd:.2f} of ${cap.limit_usd:.2f})."
            for cap in self.caps
            if cap.active and self.warn_at_percent <= cap.percent < 100
        ]


class BudgetService:
    """Computes caps against the ledger and implements ``BudgetGuardProtocol``."""

    def __init__(self, db_mgr: Any, ledger: UsageLedger) -> None:
        self._db = db_mgr
        self._ledger = ledger
        self._settings: dict[str, tuple[float, dict[str, Any]]] = {}

    async def _caps_config(self, org_id: str) -> dict[str, Any]:
        cached = self._settings.get(org_id)
        if cached and time.monotonic() - cached[0] < SETTINGS_TTL_SECONDS:
            return cached[1]
        config = dict(await effective_settings(self._db, org_id, "budgets"))
        self._settings[org_id] = (time.monotonic(), config)
        return config

    def forget(self, org_id: str) -> None:
        """Drop the cached caps (called when an administrator changes them)."""
        self._settings.pop(org_id, None)

    async def status(self, scope: ModelScope) -> BudgetStatus:
        config = await self._caps_config(scope.org_id)
        since = month_start()
        caps: list[Cap] = []
        org_limit = float(config.get("monthly_budget_usd") or 0)
        if org_limit > 0:
            spent = await self._ledger.spend_usd(scope.org_id, since=since)
            caps.append(Cap("Organization monthly", org_limit, spent))
        member_limit = float(config.get("member_monthly_budget_usd") or 0)
        if member_limit > 0:
            spent = await self._ledger.spend_usd(scope.org_id, since=since, user_id=scope.user_id)
            caps.append(Cap("Your monthly", member_limit, spent))
        session_limit = float(config.get("session_budget_usd") or 0)
        if session_limit > 0 and scope.session_id:
            spent = await self._ledger.spend_usd(scope.org_id, since=0, session_id=scope.session_id)
            caps.append(Cap("Session", session_limit, spent))
        return BudgetStatus(caps=caps, warn_at_percent=int(config.get("warn_at_percent") or 80))

    async def check(self, scope: ModelScope) -> None:
        """Raise ``BudgetExceededError`` if any cap is reached."""
        reached = (await self.status(scope)).exceeded()
        if reached is not None:
            raise BudgetExceededError(
                f"{reached.label} budget reached: ${reached.spent_usd:.2f} spent of "
                f"${reached.limit_usd:.2f}. Ask an administrator to raise it, or wait for the "
                "next period.",
                scope=reached.label,
                limit_usd=reached.limit_usd,
                spent_usd=reached.spent_usd,
            )
