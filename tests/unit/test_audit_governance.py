"""Unit tests for the governance services.

The audit store, approval policy, model data controls, retention selection, egress
translation and rate limiter are all exercised without a database, which keeps the
selection/policy logic testable in isolation from the PostgreSQL repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from api.services.audit import (
    ApprovalPolicy,
    AuditService,
    EgressPolicy,
    GovernancePolicy,
    GovernanceRateLimiter,
    ModelDataGovernance,
    RetentionPolicy,
    TokenBucketRateLimiter,
    decode_audit_cursor,
    encode_audit_cursor,
    is_expired,
)

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "008_audit.py"


class _Clock:
    """A monotonic clock the test advances by hand (no wall-clock sleeps)."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# -- approval policy (G4) ------------------------------------------------------


def test_approval_policy_defaults_to_a_human_gate() -> None:
    policy = ApprovalPolicy()

    assert policy.decision_for("critical") == "require_human"
    assert policy.decision_for("low") == "require_human"
    assert policy.decision_for(None) == "require_human"


def test_approval_policy_auto_approves_and_denies_named_risk_levels() -> None:
    policy = ApprovalPolicy(auto_approve=("low",), deny=("critical",))

    assert policy.decision_for("LOW") == "auto_approve"
    assert policy.decision_for("critical") == "deny"
    assert policy.decision_for("medium") == "require_human"


def test_approval_policy_from_settings() -> None:
    policy = ApprovalPolicy.from_settings(
        {"approval_policy": {"auto_approve": "low,medium", "deny": ["critical"]}}
    )

    assert policy.auto_approve == ("low", "medium")
    assert policy.deny == ("critical",)


# -- model data governance ---------------------------------------------


def test_model_data_governance_provider_allowlist() -> None:
    open_policy = ModelDataGovernance()
    assert open_policy.provider_allowed("openrouter/openai/gpt-4o") is True
    assert open_policy.provider_allowed("") is False

    restricted = ModelDataGovernance(provider_allowlist=("openrouter",))
    assert restricted.provider_allowed("openrouter/anthropic/claude") is True
    assert restricted.provider_allowed("azure/openai/gpt-4") is False


def test_model_data_governance_from_settings() -> None:
    governance = ModelDataGovernance.from_settings(
        {"provider_allowlist": ["openrouter"], "zero_retention": True, "region": "eu-west-1"}
    )

    assert governance.provider_allowlist == ("openrouter",)
    assert governance.zero_retention is True
    assert governance.region == "eu-west-1"


# -- retention ----------------------------------------------------------


def test_retention_cutoff_and_expiry_selection() -> None:
    now = 1_700_000_000
    policy = RetentionPolicy(trace_days=7, memory_days=None)

    cutoff = policy.cutoff("turn_traces", now)
    assert cutoff == now - 7 * 86400
    assert is_expired(now - 8 * 86400, cutoff) is True
    assert is_expired(now - 6 * 86400, cutoff) is False
    # No window configured means "keep indefinitely", never "delete now".
    assert policy.cutoff("memories", now) is None
    assert is_expired(0, None) is False
    # Unknown tables carry no retention rule.
    assert policy.cutoff("not_a_table", now) is None


def test_retention_from_settings_ignores_zero_and_malformed() -> None:
    policy = RetentionPolicy.from_settings(
        {"session_retention_days": "30", "trace_retention_days": 0, "memory_retention_days": "x"}
    )

    assert policy.session_days == 30
    assert policy.trace_days is None
    assert policy.memory_days is None


# -- egress policy (G5) --------------------------------------------------------


def test_egress_policy_translates_allowlist_to_network_policy_rules() -> None:
    policy = EgressPolicy(allowed_cidrs=("10.96.0.10/32",), block_internal_subnets=True)

    rules = policy.network_policy_egress_rules()

    assert {"to": [{"ipBlock": {"cidr": "10.96.0.10/32"}}]} in rules
    blocked = next(rule for rule in rules if rule["to"][0]["ipBlock"]["cidr"] == "0.0.0.0/0")
    assert "169.254.169.254/32" in blocked["to"][0]["ipBlock"]["except"]


def test_egress_policy_from_settings() -> None:
    policy = EgressPolicy.from_settings(
        {"egress_allowlist": "10.0.0.0/8, 192.168.1.0/24", "block_internal_subnets": True}
    )

    assert policy.allowed_cidrs == ("10.0.0.0/8", "192.168.1.0/24")
    assert policy.block_internal_subnets is True


def test_governance_policy_aggregates_all_domains() -> None:
    policy = GovernancePolicy.from_settings(
        {
            "approval_policy": {"auto_approve": ["low"]},
            "provider_allowlist": ["openrouter"],
            "trace_retention_days": 14,
            "egress_allowlist": ["10.0.0.0/8"],
        }
    )

    assert policy.approval.decision_for("low") == "auto_approve"
    assert policy.model_data.provider_allowlist == ("openrouter",)
    assert policy.retention.trace_days == 14
    assert policy.egress.allowed_cidrs == ("10.0.0.0/8",)


# -- rate limiting (G6) --------------------------------------------------------


def test_token_bucket_allows_then_denies_then_refills() -> None:
    clock = _Clock()
    limiter = TokenBucketRateLimiter(capacity=2, refill_per_second=1.0, clock=clock)

    assert limiter.check("user").allowed is True
    assert limiter.check("user").allowed is True
    denied = limiter.check("user")
    assert denied.allowed is False
    assert denied.retry_after_seconds == 1

    clock.now += 1.0
    assert limiter.check("user").allowed is True


def test_token_bucket_registry_is_bounded() -> None:
    clock = _Clock()
    limiter = TokenBucketRateLimiter(capacity=1, refill_per_second=0.0, max_keys=2, clock=clock)

    limiter.check("a")
    limiter.check("b")
    limiter.check("c")

    assert len(limiter._buckets) == 2


def test_governance_rate_limiter_scopes_user_and_org_denials() -> None:
    clock = _Clock()
    limiter = GovernanceRateLimiter(per_user_per_minute=1, per_org_per_minute=10, clock=clock)

    decision, scope = limiter.check(org_id="org_a", user_id="u1")
    assert decision.allowed is True
    assert scope == ""

    denied_user, scope = limiter.check(org_id="org_a", user_id="u1")
    assert denied_user.allowed is False
    assert scope == "api_user"

    # A different user in the same org still has its own budget.
    allowed_other, _ = limiter.check(org_id="org_a", user_id="u2")
    assert allowed_other.allowed is True


def test_governance_rate_limiter_denies_org_scope() -> None:
    clock = _Clock()
    limiter = GovernanceRateLimiter(per_user_per_minute=5, per_org_per_minute=1, clock=clock)

    assert limiter.check(org_id="org_a", user_id="u1")[0].allowed is True
    denied, scope = limiter.check(org_id="org_a", user_id="u2")

    assert denied.allowed is False
    assert scope == "api_org"


# -- audit store ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_audit_records_and_paginates() -> None:
    service = AuditService()
    await service.record(
        "tool_call",
        org_id="org_a",
        actor_user_id="u1",
        session_id="s1",
        tool_name="bash_exec",
        result_summary="bash_exec: completed",
        now=100,
    )
    await service.record(
        "approval_decision",
        org_id="org_a",
        actor_user_id="u1",
        session_id="s1",
        approved_by="u1",
        result_summary="approved",
        now=200,
    )
    await service.record("tool_call", org_id="org_b", actor_user_id="u2", now=300)

    first = await service.list_page("org_a", limit=1)
    assert len(first) == 1
    assert first[0].created_at == 200

    cursor = decode_audit_cursor(encode_audit_cursor(first[0]))
    second = await service.list_page("org_a", limit=1, cursor=cursor)
    assert second[0].created_at == 100

    # org_b's row is invisible to an org_a listing.
    assert all(event.org_id == "org_a" for event in [*first, *second])


@pytest.mark.asyncio
async def test_audit_service_rejects_unknown_event_types() -> None:
    service = AuditService()
    with pytest.raises(ValueError):
        await service.record("not_a_real_event", org_id="org_a")


def test_audit_cursor_round_trips_and_rejects_malformed() -> None:
    from api.services.audit import AuditEvent

    event = AuditEvent(id="aud_x", org_id="org_a", event_type="tool_call", created_at=42)
    assert decode_audit_cursor(encode_audit_cursor(event)) == (42, "aud_x")

    with pytest.raises(ValueError):
        decode_audit_cursor("not-base64!!")


# -- migration contract --------------------------------------------------------


def test_migration_008_declares_tenant_isolation() -> None:
    """CI provisions the schema from metadata and never runs Alembic, so the RLS policy
    the isolation test depends on is asserted here against the migration itself."""
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "008_audit"' in source
    assert 'down_revision: str | None = "007_slack_quotas"' in source
    assert "CREATE POLICY tenant_isolation_audit_events ON audit_events" in source
    assert "USING (org_id = current_setting('app.current_org_id', true))" in source
    assert "ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;" in source
    assert "DROP POLICY IF EXISTS tenant_isolation_audit_events ON audit_events;" in source
