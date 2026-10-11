"""Unit tests for Slack quota + token-bucket rate limiting.

Every test drives an injected fake clock — no wall-clock sleeps.
"""

from api.services.quota import (
    InMemoryWorkspaceQuotaStore,
    SlackWorkspaceQuotaService,
    WorkspaceQuotaState,
)
from slack_assistant.ratelimit import BoundedRateLimiter


class FakeClock:
    """A deterministic clock whose ``now`` only moves when a test moves it."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def _evaluate(
    service: SlackWorkspaceQuotaService,
    *,
    team_id: str = "T1",
    now: float | None = None,
    active_sessions: int = 0,
):
    return await service.evaluate(
        team_id=team_id,
        org_id="acme",
        user_id="U1",
        channel_id="C1",
        active_sessions=active_sessions,
        now=now,
    )


def test_rate_limiter_allows_then_denies_then_refills() -> None:
    limiter = BoundedRateLimiter(capacity=2, refill_per_second=2.0)
    clock = FakeClock()

    assert limiter.evaluate(("user:acme:U1",), clock()).allowed is True
    assert limiter.evaluate(("user:acme:U1",), clock()).allowed is True

    denied = limiter.evaluate(("user:acme:U1",), clock())
    assert denied.allowed is False
    assert denied.scope == "user"
    assert denied.retry_after_seconds == 1

    # Half a second refills exactly one token at 2 tokens/second.
    clock.advance(0.5)
    assert limiter.evaluate(("user:acme:U1",), clock()).allowed is True


def test_rate_limiter_is_bounded_by_entries() -> None:
    limiter = BoundedRateLimiter(capacity=1, refill_per_second=1.0, max_entries=3)
    for index in range(10):
        limiter.bucket(f"user:acme:{index}", now=float(index))
    assert limiter.active_buckets == 3


def test_rate_limiter_scopes_are_independent() -> None:
    limiter = BoundedRateLimiter(capacity=1, refill_per_second=0.001)
    assert limiter.evaluate(("user:acme:U1",), 0.0).allowed is True
    # A different user has its own bucket.
    assert limiter.evaluate(("user:acme:U2",), 0.0).allowed is True


async def test_workspace_quota_defaults_to_admit() -> None:
    service = SlackWorkspaceQuotaService(in_memory_store=InMemoryWorkspaceQuotaStore())
    decision = await _evaluate(service)
    assert decision.allowed is True
    assert service.counters()["turns_admitted"] == 1


async def test_workspace_hourly_quota_denies_then_expires() -> None:
    clock = FakeClock(1000.0)
    store = InMemoryWorkspaceQuotaStore()
    store.set_state("T1", WorkspaceQuotaState(turns_per_hour=2))
    service = SlackWorkspaceQuotaService(in_memory_store=store, clock=clock)

    assert (await _evaluate(service, now=clock())).allowed is True
    assert (await _evaluate(service, now=clock())).allowed is True

    denied = await _evaluate(service, now=clock())
    assert denied.allowed is False
    assert denied.reason == "turns_per_hour"
    assert denied.retry_after_seconds == 3600

    clock.advance(3600)
    assert (await _evaluate(service, now=clock())).allowed is True
    assert service.counters()["quota_denied"] == 1


async def test_workspace_budget_cap_denies() -> None:
    store = InMemoryWorkspaceQuotaStore()
    store.set_state(
        "T1",
        WorkspaceQuotaState(monthly_usd_cap=10.0, current_month_spend_usd=10.5),
    )
    service = SlackWorkspaceQuotaService(in_memory_store=store)
    decision = await _evaluate(service, now=0.0)
    assert decision.allowed is False
    assert decision.reason == "budget"


async def test_workspace_concurrency_cap_denies() -> None:
    store = InMemoryWorkspaceQuotaStore()
    store.set_state("T1", WorkspaceQuotaState(max_concurrent_sessions=1))
    service = SlackWorkspaceQuotaService(in_memory_store=store)
    decision = await _evaluate(service, now=0.0, active_sessions=1)
    assert decision.allowed is False
    assert decision.reason == "concurrency"


async def test_missing_team_id_is_admitted_to_keep_rate_limit_authoritative() -> None:
    service = SlackWorkspaceQuotaService(in_memory_store=InMemoryWorkspaceQuotaStore())
    decision = await service.evaluate(
        team_id="", org_id="acme", user_id=None, channel_id="C1", active_sessions=0
    )
    assert decision.allowed is True
