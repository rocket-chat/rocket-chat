"""Spend limits: which apply, their windows, and atomic reservation (P3.4)."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest
from api.services.budgets import BudgetService
from api.services.spend_limits import SpendLimit, applicable, window
from api.services.usage import UsageLedger

from specifications.interfaces.llm import BudgetExceededError, ModelScope


def _limit(**fields: object) -> SpendLimit:
    base: dict[str, object] = {
        "id": "lim",
        "org_id": "o",
        "subject_kind": "org",
        "subject_id": None,
        "member_id": None,
        "period": "month",
        "amount_usd": 10.0,
        "mode": "block",
        "warn_at_percent": 80,
        "is_ceiling": False,
        "created_by": "admin",
        "created_at": 0,
        "updated_at": 0,
    }
    base.update(fields)
    return SpendLimit(**base)  # type: ignore[arg-type]


SCOPE = ModelScope(
    org_id="o",
    user_id="owner",
    requester_user_id="alice",
    agent_id="agent-pro",
    session_id="s1",
    turn_id="t1",
)


def test_calendar_and_rolling_windows() -> None:
    moment = datetime(2026, 10, 29, 15, 30, tzinfo=UTC).timestamp()  # a Thursday
    day = window("day", moment)
    assert datetime.fromtimestamp(day.current, UTC).hour == 0
    assert day.resets_at == day.current + 86_400
    week = window("week", moment)
    assert datetime.fromtimestamp(week.current, UTC).weekday() == 0
    month = window("month", moment)
    assert datetime.fromtimestamp(month.current, UTC).day == 1
    assert datetime.fromtimestamp(month.resets_at or 0, UTC).month == 11
    rolling = window("rolling_24h", moment)
    assert rolling.current - rolling.since == 23 * 3600
    assert window("session", moment).current == 0


def test_which_limits_apply_and_to_which_counter() -> None:
    limits = [
        _limit(id="org"),
        _limit(id="each", subject_kind="member"),
        _limit(id="bob", subject_kind="member", subject_id="bob"),
        _limit(id="agent", subject_kind="agent", subject_id="agent-pro"),
        _limit(id="other-agent", subject_kind="agent", subject_id="agent-cheap"),
        _limit(id="pair", subject_kind="agent_member", subject_id="agent-pro"),
        _limit(id="sess", period="session"),
        _limit(id="turn", period="turn"),
    ]
    applied = {a.limit.id: a.subject_key for a in applicable(limits, SCOPE, "platform")}
    assert applied == {
        "org": "org",
        "each": "alice",  # the requester, not the session's owner
        "agent": "agent-pro",
        "pair": "agent-pro:alice",
        "sess": "org|session:s1",
        "turn": "org|turn:t1",
    }


def test_a_members_own_key_counts_against_nothing() -> None:
    assert applicable([_limit()], SCOPE, "user") == []
    assert applicable([_limit()], SCOPE, "agent")


def test_an_explicit_member_limit_replaces_the_default_under_its_ceiling() -> None:
    limits = [
        _limit(id="each", subject_kind="member", amount_usd=20.0, is_ceiling=True),
        _limit(id="alice", subject_kind="member", subject_id="alice", amount_usd=50.0),
    ]
    applied = applicable(limits, SCOPE, "platform")
    assert [(a.limit.id, a.amount_usd) for a in applied] == [("alice", 20.0)]
    limits[0] = _limit(id="each", subject_kind="member", amount_usd=20.0, is_ceiling=False)
    applied = applicable(limits, SCOPE, "platform")
    assert [(a.limit.id, a.amount_usd) for a in applied] == [("alice", 50.0)]


def test_a_slack_workspace_limit_applies_to_its_guests() -> None:
    scope = ModelScope(org_id="o", user_id="slack:T1:U9")
    limits = [_limit(id="ws", subject_kind="slack_workspace", subject_id="T1")]
    assert [a.subject_key for a in applicable(limits, scope, "platform")] == ["T1"]
    assert applicable(limits, ModelScope(org_id="o", user_id="slack:T2:U9"), "platform") == []


async def _service(*limits: SpendLimit) -> BudgetService:
    service = BudgetService(None, UsageLedger())
    for limit in limits:
        await service.store.create(limit)
    return service


async def test_a_block_limit_refuses_the_call_that_would_cross_it() -> None:
    service = await _service(_limit(amount_usd=1.0))
    first = await service.reserve(SCOPE, estimate_usd=0.6, key_source="platform")
    assert first is not None
    await service.settle(first, actual_usd=0.6)
    with pytest.raises(BudgetExceededError) as refused:
        await service.reserve(SCOPE, estimate_usd=0.6, key_source="platform")
    assert refused.value.limit_id == "lim"
    assert refused.value.period == "month"
    assert refused.value.resets_at is not None


async def test_a_warn_limit_never_refuses() -> None:
    service = await _service(_limit(mode="warn", amount_usd=1.0))
    for _ in range(3):
        reservation = await service.reserve(SCOPE, estimate_usd=0.6, key_source="platform")
        assert reservation is not None
        await service.settle(reservation, actual_usd=0.6)
    status = await service.status(SCOPE)
    assert status.warnings() and status.warnings()[0].spent_usd == pytest.approx(1.8)


async def test_twenty_parallel_calls_never_overshoot_by_more_than_one_estimate() -> None:
    service = await _service(_limit(amount_usd=1.0))
    estimate = 0.15

    async def call() -> bool:
        try:
            reservation = await service.reserve(SCOPE, estimate_usd=estimate, key_source="platform")
        except BudgetExceededError:
            return False
        await asyncio.sleep(0)
        assert reservation is not None
        await service.settle(reservation, actual_usd=estimate)
        return True

    admitted = sum(await asyncio.gather(*(call() for _ in range(20))))
    spent = (await service.status(SCOPE)).limits[0].spent_usd
    assert admitted == 6
    assert spent <= 1.0 + estimate


async def test_settle_replaces_the_estimate_with_the_real_cost() -> None:
    service = await _service(_limit(amount_usd=10.0))
    reservation = await service.reserve(SCOPE, estimate_usd=5.0, key_source="platform")
    assert reservation is not None
    assert (await service.status(SCOPE)).limits[0].spent_usd == pytest.approx(5.0)
    await service.settle(reservation, actual_usd=0.25)
    assert (await service.status(SCOPE)).limits[0].spent_usd == pytest.approx(0.25)


async def test_a_crashed_calls_reservation_is_released() -> None:
    service = await _service(_limit(amount_usd=1.0))
    await service.reserve(SCOPE, estimate_usd=0.9, key_source="platform")
    with pytest.raises(BudgetExceededError):
        await service.reserve(SCOPE, estimate_usd=0.5, key_source="platform")
    assert await service.release_stale(older_than=-1) == 1
    assert await service.reserve(SCOPE, estimate_usd=0.5, key_source="platform") is not None


async def test_a_rolling_window_forgets_spend_older_than_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = await _service(_limit(period="rolling_24h", amount_usd=1.0))
    start = time.time()
    monkeypatch.setattr(time, "time", lambda: start)
    reservation = await service.reserve(SCOPE, estimate_usd=0.9, key_source="platform")
    assert reservation is not None
    await service.settle(reservation, actual_usd=0.9)
    monkeypatch.setattr(time, "time", lambda: start + 3600)
    with pytest.raises(BudgetExceededError):
        await service.reserve(SCOPE, estimate_usd=0.5, key_source="platform")
    monkeypatch.setattr(time, "time", lambda: start + 25 * 3600)
    assert await service.reserve(SCOPE, estimate_usd=0.5, key_source="platform") is not None


async def test_an_agent_for_a_member_limit() -> None:
    service = await _service(
        _limit(subject_kind="agent_member", subject_id="agent-pro", amount_usd=1.0)
    )
    reservation = await service.reserve(SCOPE, estimate_usd=0.8, key_source="platform")
    assert reservation is not None
    await service.settle(reservation, actual_usd=0.8)
    with pytest.raises(BudgetExceededError):
        await service.reserve(SCOPE, estimate_usd=0.5, key_source="platform")
    bob = ModelScope(org_id="o", user_id="owner", requester_user_id="bob", agent_id="agent-pro")
    assert await service.reserve(bob, estimate_usd=0.5, key_source="platform") is not None
