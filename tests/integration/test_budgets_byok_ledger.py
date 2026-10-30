"""Budgets, bring-your-own-key and the persisted usage ledger, end to end.

The gateway is the real ``LiteLLMGateway`` with only ``litellm.acompletion`` replaced, so the
policy code that matters - cap checks, key substitution, ledger writes - runs for real. The
ledger and settings are in-process unless a test needs PostgreSQL.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import litellm
import pytest
from api.services.budgets import BudgetService
from api.services.model_credentials import (
    CredentialResolver,
    ModelCredentialStore,
    NewCredential,
    provider_of,
)
from api.services.settings import reset_memory_settings
from api.services.spend_limits import SpendLimit
from api.services.usage import UsageLedger, month_start
from llm_gateway.gateway import LiteLLMGateway

from specifications.interfaces.llm import (
    ChatMessage,
    ModelRequest,
    ModelScope,
    UsageEvent,
    current_model_scope,
)

ORG, USER, OTHER = "org_budget", "member_a", "member_b"


def _chunk(text: str, usage: dict[str, int] | None = None) -> Any:
    chunk = MagicMock()
    choice = MagicMock()
    choice.delta.content = text
    choice.delta.reasoning_content = None
    choice.delta.thinking = None
    choice.delta.tool_calls = None
    choice.finish_reason = "stop"
    chunk.choices = [choice]
    chunk.usage = MagicMock(**usage) if usage else None
    return chunk


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the provider call; records every call's kwargs."""
    calls: list[dict[str, Any]] = []

    async def acompletion(**kwargs: Any) -> AsyncIterator[Any]:
        calls.append(kwargs)

        async def stream() -> AsyncIterator[Any]:
            yield _chunk("hello world")

        return stream()

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    return calls


@pytest.fixture
def stack() -> tuple[LiteLLMGateway, UsageLedger, BudgetService, ModelCredentialStore]:
    reset_memory_settings()
    ledger = UsageLedger(None)
    store = ModelCredentialStore(None)
    budget = BudgetService(None, ledger)
    gateway = LiteLLMGateway()
    gateway.usage_sink = ledger
    gateway.budget_guard = budget
    gateway.credential_resolver = CredentialResolver(None, store)
    return gateway, ledger, budget, store


async def _call(gateway: LiteLLMGateway, user: str = USER, session: str = "s1") -> list[Any]:
    token = current_model_scope.set(ModelScope(ORG, user, session))
    try:
        request = ModelRequest(
            model="openrouter/anthropic/claude-sonnet-5.5",
            messages=[ChatMessage(role="user", content="hi there")],
        )
        return [chunk async for chunk in gateway.chat_stream(request)]
    finally:
        current_model_scope.reset(token)


# -- ledger ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_call_is_written_to_the_ledger_with_its_cost(
    provider: list[dict[str, Any]], stack: Any
) -> None:
    gateway, ledger, *_ = stack

    await _call(gateway)

    summary = await ledger.summary(ORG, since=month_start())
    assert summary["calls"] == 1 and summary["total_tokens"] > 0
    assert summary["total_cost_usd"] > 0, "a call is never accounted as free"
    assert "openrouter/anthropic/claude-sonnet-5.5" in summary["models"]


@pytest.mark.asyncio
async def test_a_failed_call_is_not_billed(stack: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, ledger, *_ = stack

    async def boom(**_: Any) -> Any:
        raise RuntimeError("provider down")

    monkeypatch.setattr(litellm, "acompletion", boom)
    chunks = await _call(gateway)

    assert chunks[-1].error is not None
    assert (await ledger.summary(ORG, since=0))["calls"] == 0


# -- spend limits ---------------------------------------------------------------------------


def _limit(
    amount: float,
    *,
    subject: str = "org",
    subject_id: str | None = None,
    period: str = "month",
    mode: str = "block",
) -> SpendLimit:
    return SpendLimit(
        id=f"lim_{subject}_{period}_{amount}",
        org_id=ORG,
        subject_kind=subject,  # type: ignore[arg-type]
        subject_id=subject_id,
        member_id=None,
        period=period,  # type: ignore[arg-type]
        amount_usd=amount,
        mode=mode,  # type: ignore[arg-type]
        warn_at_percent=80,
        is_ceiling=False,
        created_by="admin",
        created_at=0,
        updated_at=0,
    )


async def _spend(
    budget: BudgetService, amount: float, user: str = OTHER, session: str = "s0"
) -> None:
    """Spend counted on the limits that apply, as a settled call does."""
    reservation = await budget.reserve(
        ModelScope(ORG, user, session), estimate_usd=amount, key_source="platform"
    )
    if reservation is not None:
        await budget.settle(reservation, actual_usd=amount)


@pytest.mark.asyncio
async def test_a_turn_over_its_limit_makes_zero_provider_calls(
    provider: list[dict[str, Any]], stack: Any
) -> None:
    gateway, _, budget, _ = stack
    await budget.store.create(_limit(1.0))
    await _spend(budget, 0.999)

    chunks = await _call(gateway)

    assert provider == [], "no tokens may be bought once the limit is reached"
    assert chunks[-1].error is not None and chunks[-1].refusal is not None


@pytest.mark.asyncio
async def test_under_the_limit_calls_go_through_and_no_limit_means_none(
    provider: list[dict[str, Any]], stack: Any
) -> None:
    gateway, _, budget, _ = stack
    await _call(gateway)  # no limit configured
    await budget.store.create(_limit(5000.0))
    await _call(gateway)

    assert len(provider) == 2


@pytest.mark.asyncio
async def test_a_session_limit_applies_to_that_session_only(
    provider: list[dict[str, Any]], stack: Any
) -> None:
    gateway, _, budget, _ = stack
    await budget.store.create(_limit(0.5, period="session"))
    await _spend(budget, 0.4999, user=USER, session="busy")

    busy = await _call(gateway, session="busy")
    fresh = await _call(gateway, session="another")

    assert busy[-1].error is not None
    assert fresh[-1].error is None


# -- credentials ----------------------------------------------------------------------------


async def _key(
    store: ModelCredentialStore, owner: str | None, api_key: str, api_base: str | None = None
) -> None:
    await store.create(
        ORG,
        NewCredential(
            "member" if owner else "org", owner, "openrouter", api_key, api_base=api_base
        ),
    )


@pytest.mark.asyncio
async def test_the_members_own_key_is_used_and_recorded_as_theirs(
    provider: list[dict[str, Any]], stack: Any
) -> None:
    gateway, ledger, _, store = stack
    gateway.credential_resolver = CredentialResolver(None, store, member_may_use_keys=_may)
    await _key(store, None, "sk-or-ORG-KEY-1234")
    await _key(store, USER, "sk-or-USER-KEY-5678", "https://proxy.example/v1")

    await _call(gateway)
    await _call(gateway, user=OTHER)

    assert provider[0]["api_key"] == "sk-or-USER-KEY-5678"
    assert provider[0]["api_base"] == "https://proxy.example/v1"
    assert provider[1]["api_key"] == "sk-or-ORG-KEY-1234", "other members fall back to the org key"
    summary = await ledger.summary(ORG, since=0)
    assert summary["own_key_cost_usd"] > 0


async def _may(org_id: str, user_id: str) -> bool:
    return True


@pytest.mark.asyncio
async def test_platform_keys_are_used_when_nobody_stored_one(
    provider: list[dict[str, Any]], stack: Any
) -> None:
    gateway, *_ = stack

    await _call(gateway)

    assert "api_key" not in provider[0]


@pytest.mark.asyncio
async def test_an_organization_key_replaces_the_deployments_environment_key(
    provider: list[dict[str, Any]], stack: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, _, _, store = stack
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-DEPLOYMENT-1111")

    await _call(gateway)
    await _key(store, None, "sk-or-ORG-KEY-1234")
    await _call(gateway)

    assert "api_key" not in provider[0], "the environment key is litellm's own fallback"
    assert provider[1]["api_key"] == "sk-or-ORG-KEY-1234"


@pytest.mark.asyncio
async def test_keys_are_encrypted_at_rest_and_only_a_fingerprint_is_listed() -> None:
    store = ModelCredentialStore(None)
    created = await store.create(
        ORG, NewCredential("member", USER, "anthropic", "sk-ant-api03-SECRETSECRET1234")
    )

    assert created.fingerprint.endswith("1234") and "SECRET" not in created.fingerprint
    assert "SECRET" not in repr(store._memory)
    listed = await store.list(ORG, owner_kind="member", owner_user_id=USER)
    assert [(k.provider, k.id) for k in listed] == [("anthropic", created.id)]
    assert "SECRET" not in repr(listed)
    secret = await store.secret(ORG, created.id)
    assert secret is not None and secret.api_key.endswith("SECRETSECRET1234")
    assert await store.delete(ORG, created.id) is not None
    assert await store.list(ORG, owner_kind="member", owner_user_id=USER) == []


def test_the_provider_is_derived_from_the_model_id() -> None:
    assert provider_of("openrouter/anthropic/claude-3.7-sonnet") == "openrouter"
    assert provider_of("anthropic/claude-3-7-sonnet-20250219") == "anthropic"
    assert provider_of("gpt-4o") == "openai"


# -- PostgreSQL: the ledger is persisted, tenant-isolated and shared -------------------------

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")


@pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL not configured")
@pytest.mark.asyncio
async def test_the_ledger_survives_a_restart_and_is_tenant_isolated(tenant_rows: None) -> None:
    from api.db.session import DatabaseSessionManager

    first = DatabaseSessionManager(TEST_DB_URL)
    await UsageLedger(first).record(
        UsageEvent("test_org", "member_user", "m", 100, 50, cost_usd=1.25, session_id="slack_C1_1")
    )
    await UsageLedger(first).record(
        UsageEvent("test_org", "member_user", "m", 1, 1, cost_usd=9.0, key_source="user")
    )
    await first.close()

    restarted = UsageLedger(DatabaseSessionManager(TEST_DB_URL))  # a different process, later
    assert await restarted.spend_usd("test_org", since=month_start()) == 1.25
    assert (
        await restarted.spend_usd("test_org", since=month_start(), platform_funded_only=False)
        == 10.25
    )
    assert (
        await restarted.spend_usd("test_org", since=month_start(), session_prefix="slack_") == 1.25
    )
    assert await restarted.spend_usd("usage_org", since=0) == 0.0, "another tenant sees nothing"


@pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL not configured")
@pytest.mark.asyncio
async def test_a_sessions_calls_come_back_in_order_and_only_for_its_organization(
    tenant_rows: None,
) -> None:
    from api.db.session import DatabaseSessionManager

    manager = DatabaseSessionManager(TEST_DB_URL)
    ledger = UsageLedger(manager)
    for at, session_id, org in (
        (30, "sess_usage_a", "test_org"),
        (10, "sess_usage_a", "test_org"),
        (20, "sess_usage_b", "test_org"),
        (15, "sess_usage_a", "usage_org"),
    ):
        await ledger.record(
            UsageEvent(org, "member_user", "m", 100, 10, 0.5, session_id, 40, 0, "platform", at)
        )

    events = await ledger.session_events("test_org", "sess_usage_a")

    assert [e["created_at"] for e in events] == [10, 30], "oldest first, this session only"
    assert events[0]["cache_read_tokens"] == 40 and events[0]["cost_usd"] == 0.5
    assert await ledger.session_events("usage_org", "sess_usage_a") != events, "per organization"
    await manager.close()


# -- through the real turn pipeline ---------------------------------------------------------


async def _run_turn(
    prompt: str = "hello there",
) -> tuple[Any, UsageLedger, list[Any], str]:
    """A whole web turn on the stub model with the real ledger and budget guard attached."""
    from agent_core.orchestrator import AsyncReActOrchestrator
    from agent_core.tools.registry import ToolRegistry
    from api.events import AsyncIOEventBus
    from api.services.transcript import InMemoryTranscriptStore
    from api.store import InMemorySessionStore
    from api.turns.tasks import run_guarded_turn
    from llm_gateway.stub import DeterministicStubGateway

    from specifications.interfaces.session import SessionRecord

    ledger = UsageLedger(None)
    gateway = DeterministicStubGateway()
    gateway.usage_sink = ledger
    gateway.budget_guard = BudgetService(None, ledger)
    registry = ToolRegistry()
    orchestrator = AsyncReActOrchestrator(
        gateway=gateway, registry=registry, transcript_store=InMemoryTranscriptStore()
    )
    bus, store = AsyncIOEventBus(), InMemorySessionStore()
    session_id = "budget_session"
    await store.create_session(
        SessionRecord(session_id=session_id, tenant_org_id=ORG, tenant_user_id=USER, title="t")
    )
    queue, close = bus.open_queue(session_id)
    try:
        await run_guarded_turn(
            session_id=session_id,
            prompt=prompt,
            orchestrator=orchestrator,
            event_bus=bus,
            driver=None,
            registry=registry,
            store=store,
            author_user_id=USER,
        )
    finally:
        close()
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return gateway, ledger, events, session_id


@pytest.mark.asyncio
async def test_a_turn_runs_and_its_usage_is_attributed_to_org_member_and_session() -> None:
    reset_memory_settings()

    gateway, ledger, _, session_id = await _run_turn()

    assert gateway.call_count >= 1
    entries = ledger._memory
    assert entries and {(e.org_id, e.user_id, e.session_id) for e in entries} == {
        (ORG, USER, session_id)
    }
    assert all(e.cost_usd > 0 for e in entries)
