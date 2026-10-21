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
from api.services.model_credentials import CredentialResolver, ModelCredentialStore, provider_of
from api.services.settings import reset_memory_settings, save_org_config
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
            model="openrouter/anthropic/claude-3.7-sonnet",
            messages=[ChatMessage(role="user", content="hi there")],
        )
        return [chunk async for chunk in gateway.chat_stream(request)]
    finally:
        current_model_scope.reset(token)


async def _cap(**values: float | int | str) -> None:
    await save_org_config(None, ORG, "budgets", dict(values), [])


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
    assert "openrouter/anthropic/claude-3.7-sonnet" in summary["models"]


@pytest.mark.asyncio
async def test_a_failed_call_is_not_billed(stack: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, ledger, *_ = stack

    async def boom(**_: Any) -> Any:
        raise RuntimeError("provider down")

    monkeypatch.setattr(litellm, "acompletion", boom)
    chunks = await _call(gateway)

    assert chunks[-1].error is not None
    assert (await ledger.summary(ORG, since=0))["calls"] == 0


# -- budgets --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_turn_over_budget_makes_zero_provider_calls(
    provider: list[dict[str, Any]], stack: Any
) -> None:
    gateway, ledger, *_ = stack
    await _cap(monthly_budget_usd=1.0)
    await ledger.record(UsageEvent(ORG, OTHER, "m", 10, 10, cost_usd=1.5))

    chunks = await _call(gateway)

    assert provider == [], "no tokens may be bought once the cap is reached"
    assert (
        chunks[-1].error is not None and "Organization monthly budget reached" in chunks[-1].error
    )
    assert "$1.50 spent of $1.00" in chunks[-1].error


@pytest.mark.asyncio
async def test_under_budget_calls_go_through_and_no_cap_means_no_limit(
    provider: list[dict[str, Any]], stack: Any
) -> None:
    gateway, ledger, *_ = stack
    await ledger.record(UsageEvent(ORG, OTHER, "m", 10, 10, cost_usd=1_000.0))

    await _call(gateway)  # no cap configured (0 = unlimited)
    await _cap(monthly_budget_usd=5000.0)
    await _call(gateway)

    assert len(provider) == 2


@pytest.mark.asyncio
async def test_member_and_session_caps_apply_independently(
    provider: list[dict[str, Any]], stack: Any
) -> None:
    gateway, ledger, *_ = stack
    await _cap(member_monthly_budget_usd=2.0, session_budget_usd=0.5)
    await ledger.record(UsageEvent(ORG, USER, "m", 1, 1, cost_usd=0.6, session_id="busy"))

    busy = await _call(gateway, session="busy")  # the session's own cap is hit
    fresh = await _call(gateway, session="another")  # same member, other session: fine
    other_member = await _call(gateway, user=OTHER, session="busy")  # session cap is per session

    assert busy[-1].error and "Session budget reached" in busy[-1].error
    assert fresh[-1].error is None
    assert other_member[-1].error and "Session budget reached" in other_member[-1].error
    await ledger.record(UsageEvent(ORG, USER, "m", 1, 1, cost_usd=2.0, session_id="x"))
    capped = await _call(gateway, session="brand-new")
    assert capped[-1].error and "Your monthly budget reached" in capped[-1].error


@pytest.mark.asyncio
async def test_spend_on_a_members_own_key_does_not_count_against_the_org(
    provider: list[dict[str, Any]], stack: Any
) -> None:
    gateway, ledger, *_ = stack
    await _cap(monthly_budget_usd=1.0)
    await ledger.record(UsageEvent(ORG, OTHER, "m", 1, 1, cost_usd=5.0, key_source="user"))

    chunks = await _call(gateway)

    assert chunks[-1].error is None and len(provider) == 1


@pytest.mark.asyncio
async def test_warnings_appear_before_the_cap_is_reached(stack: Any) -> None:
    _, ledger, budget, _ = stack
    await _cap(monthly_budget_usd=10.0, warn_at_percent=80)
    await ledger.record(UsageEvent(ORG, OTHER, "m", 1, 1, cost_usd=8.5))

    status = await budget.status(ModelScope(ORG, USER, "s"))

    assert status.exceeded() is None
    assert status.warnings() == ["Organization monthly budget is 85% used ($8.50 of $10.00)."]


@pytest.mark.asyncio
async def test_a_changed_cap_applies_after_the_cache_is_dropped(
    provider: list[dict[str, Any]], stack: Any
) -> None:
    gateway, ledger, budget, _ = stack
    await _cap(monthly_budget_usd=100.0)
    await ledger.record(UsageEvent(ORG, OTHER, "m", 1, 1, cost_usd=50.0))
    assert (await _call(gateway))[-1].error is None

    await _cap(monthly_budget_usd=10.0)
    budget.forget(ORG)

    assert (await _call(gateway))[-1].error is not None


# -- bring your own key ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_members_own_key_is_used_and_recorded_as_theirs(
    provider: list[dict[str, Any]], stack: Any
) -> None:
    gateway, ledger, _, store = stack
    await store.put(ORG, None, "openrouter", "sk-or-ORG-KEY-1234")
    await store.put(ORG, USER, "openrouter", "sk-or-USER-KEY-5678", "https://proxy.example/v1")

    await _call(gateway)
    await _call(gateway, user=OTHER)

    assert provider[0]["api_key"] == "sk-or-USER-KEY-5678"
    assert provider[0]["api_base"] == "https://proxy.example/v1"
    assert provider[1]["api_key"] == "sk-or-ORG-KEY-1234", "other members fall back to the org key"
    summary = await ledger.summary(ORG, since=0)
    assert summary["own_key_cost_usd"] > 0


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
    await store.put(ORG, None, "openrouter", "sk-or-ORG-KEY-1234")
    store._cache.clear()
    await _call(gateway)

    assert "api_key" not in provider[0], "the environment key is litellm's own fallback"
    assert provider[1]["api_key"] == "sk-or-ORG-KEY-1234"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "expected"),
    [("platform_only", None), ("org_key", "ORG"), ("user_keys", "USER")],
)
async def test_the_org_policy_decides_whose_keys_may_be_used(
    provider: list[dict[str, Any]], stack: Any, policy: str, expected: str | None
) -> None:
    gateway, _, _, store = stack
    await store.put(ORG, None, "openrouter", "sk-or-ORG-KEY-1234")
    await store.put(ORG, USER, "openrouter", "sk-or-USER-KEY-5678")
    await _cap(byok_policy=policy)

    await _call(gateway)

    used = provider[0].get("api_key")
    assert used == (
        None
        if expected is None
        else f"sk-or-{expected}-KEY-{'1234' if expected == 'ORG' else '5678'}"
    )


@pytest.mark.asyncio
async def test_keys_are_encrypted_at_rest_and_only_a_fingerprint_is_listed() -> None:
    store = ModelCredentialStore(None)
    key = await store.put(ORG, USER, "anthropic", "sk-ant-api03-SECRETSECRET1234")

    assert key.fingerprint == "sk-...1234"
    stored = store._memory[(ORG, USER, "anthropic")]
    assert "SECRET" not in stored["encrypted"]
    listed = await store.list(ORG, USER)
    assert [(k.provider, k.fingerprint) for k in listed] == [("anthropic", "sk-...1234")]
    assert "SECRET" not in repr(listed)
    assert (await store.credentials(ORG, USER, "anthropic")).api_key.endswith("SECRETSECRET1234")  # type: ignore[union-attr]
    assert await store.delete(ORG, USER, "anthropic") and await store.list(ORG, USER) == []


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


@pytest.mark.asyncio
async def test_an_over_budget_turn_is_refused_before_any_model_call_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_memory_settings()
    await _cap(session_budget_usd=0.01)
    original = UsageLedger.spend_usd

    async def spent(self: UsageLedger, org_id: str, **kwargs: Any) -> float:
        # Earlier spend in this session, as the persisted ledger would report it.
        return 0.02 if kwargs.get("session_id") else await original(self, org_id, **kwargs)

    monkeypatch.setattr(UsageLedger, "spend_usd", spent)
    gateway, _, events, _ = await _run_turn()

    assert gateway.call_count == 0, "zero model calls once the cap is reached"
    messages = " ".join(str(e.payload) for e in events)
    assert "Session budget reached" in messages
    assert "$0.02 spent of $0.01" in messages


@pytest.mark.asyncio
async def test_the_member_sees_a_warning_before_the_cap_is_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_memory_settings()
    await _cap(monthly_budget_usd=10.0, warn_at_percent=50)

    async def spent(self: UsageLedger, org_id: str, **kwargs: Any) -> float:
        return 6.0

    monkeypatch.setattr(UsageLedger, "spend_usd", spent)
    gateway, _, events, _ = await _run_turn()

    warnings = [e.payload for e in events if e.payload.get("status") == "budget_warning"]
    assert warnings and "60% used" in warnings[0]["message"]
    assert gateway.call_count >= 1, "a warning does not stop the turn"


# -- Slack workspace spend ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_slack_workspace_is_stopped_by_its_spend_from_the_ledger() -> None:
    from api.services.quota import (
        InMemoryWorkspaceQuotaStore,
        SlackWorkspaceQuotaService,
        WorkspaceQuotaState,
    )

    ledger = UsageLedger(None)
    await ledger.record(
        UsageEvent(ORG, "slack_global", "m", 1, 1, cost_usd=3.0, session_id="slack_C1_1")
    )
    await ledger.record(UsageEvent(ORG, USER, "m", 1, 1, cost_usd=40.0, session_id="web_session"))

    async def slack_spend(org_id: str) -> float:
        return await ledger.spend_usd(org_id, since=month_start(), session_prefix="slack_")

    store = InMemoryWorkspaceQuotaStore(spend=slack_spend)
    store.set_state("T1", WorkspaceQuotaState(monthly_usd_cap=2.0))
    store.set_state("T2", WorkspaceQuotaState(monthly_usd_cap=10.0))
    service = SlackWorkspaceQuotaService(in_memory_store=store)

    over = await service.evaluate(
        team_id="T1", org_id=ORG, user_id="U", channel_id="C", active_sessions=0
    )
    under = await service.evaluate(
        team_id="T2", org_id=ORG, user_id="U", channel_id="C", active_sessions=0
    )

    assert over.allowed is False and over.reason == "budget", (
        "web spend must not count, Slack spend must"
    )
    assert under.allowed is True
