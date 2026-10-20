"""General, model, routing and budget settings change what a turn does."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from api.services.budgets import BudgetService
from api.services.settings import save_org_config
from api.services.usage import UsageLedger
from api.turns.prompt import _organization_guidance
from api.turns.shared import _turn_service

from specifications.interfaces.llm import BudgetExceededError, ModelScope, UsageEvent
from tests.turn_harness import run_turn


async def _plan(org: str, **kwargs: Any) -> Any:
    return await _turn_service.plan_turn(
        db_mgr=None,
        org_id=org,
        user_id="u1",
        agent_id=None,
        global_default_model=kwargs.pop("global_default_model", "platform/default"),
        **kwargs,
    )


# --- general ---------------------------------------------------------------------------


@pytest.mark.setting("general.company_name")
async def test_the_company_name_is_told_to_the_agent() -> None:
    org = "wire_company"
    ctx = SimpleNamespace(
        session=SimpleNamespace(tenant_org_id=org, git_repo=None, git_branch=None),
        req=SimpleNamespace(db_mgr=None),
        binding=SimpleNamespace(policy=SimpleNamespace(git=SimpleNamespace(default_branch="main"))),
    )

    assert not any("Organization:" in line for line in await _organization_guidance(ctx, {}))

    await save_org_config(None, org, "general", {"company_name": "Acme Corp"}, [])
    lines = await _organization_guidance(ctx, {})
    assert "- Organization: Acme Corp." in lines


@pytest.mark.setting("general.max_turns")
async def test_the_organization_turn_limit_bounds_the_turn_unless_the_agent_sets_its_own() -> None:
    org = "wire_max_turns"
    await save_org_config(None, org, "general", {"max_turns": 7}, [])

    assert (await _plan(org)).max_turns == 7
    assert (await _plan("wire_max_turns_other")).max_turns != 7


@pytest.mark.setting("general.output_offload_threshold")
async def test_output_offload_threshold_is_carried_on_turn_policy() -> None:
    org = "wire_offload_threshold"
    await save_org_config(None, org, "general", {"output_offload_threshold": 1200}, [])

    plan = await _plan(org)
    assert plan.policy.output_offload_threshold == 1200

    plan_default = await _plan("wire_offload_threshold_other")
    assert plan_default.policy.output_offload_threshold == 6000


# --- models ----------------------------------------------------------------------------


@pytest.mark.setting("models.default_model")
async def test_the_organization_default_model_beats_the_platform_default() -> None:
    org = "wire_default_model"
    await save_org_config(None, org, "models", {"default_model": "org/chosen"}, [])

    plan = await _plan(org)

    assert plan.model == "org/chosen"
    assert "organization model policy" in plan.routing_reason


@pytest.mark.setting("models.allowed_models")
async def test_a_model_outside_the_allowlist_is_never_used() -> None:
    org = "wire_allowed_models"
    await save_org_config(
        None, org, "models", {"allowed_models": ["approved/*"], "default_model": "approved/m"}, []
    )

    plan = await _plan(org, request_model="rogue/model")

    assert plan.model == "approved/m"


@pytest.mark.setting("models.forbidden_models")
async def test_a_forbidden_model_is_skipped_even_when_requested() -> None:
    org = "wire_forbidden_models"
    await save_org_config(None, org, "models", {"forbidden_models": ["banned/*"]}, [])

    plan = await _plan(org, request_model="banned/model")

    assert plan.model == "platform/default"


@pytest.mark.setting("models.fallback_models")
@pytest.mark.setting("models.temperature")
@pytest.mark.setting("models.max_tokens")
async def test_generation_parameters_and_fallbacks_reach_the_turn_plan() -> None:
    org = "wire_generation"
    await save_org_config(
        None,
        org,
        "models",
        {
            "temperature": 0.9,
            "max_tokens": 777,
            "fallback_models": ["ok/a", "banned/b"],
            "forbidden_models": ["banned/*"],
        },
        [],
    )

    generation = (await _plan(org)).policy.generation

    assert (generation.temperature, generation.max_tokens) == (0.9, 777)
    assert generation.fallback_models == ("ok/a",)


@pytest.mark.setting("models.output_tokens")
async def test_output_tokens_setting_overrides_max_tokens() -> None:
    org = "wire_output_tokens"
    await save_org_config(
        None,
        org,
        "models",
        {
            "max_tokens": 4096,
            "output_tokens": {"openai/*": 16000, "o3-mini": 32000},
        },
        [],
    )

    plan_exact = await _plan(org, request_model="openai/o3-mini")
    assert plan_exact.policy.generation.max_tokens == 32000
    assert plan_exact.policy.generation.reasoning is not None
    assert plan_exact.policy.generation.reasoning.effort == "medium"

    plan_wildcard = await _plan(org, request_model="openai/gpt-4o")
    assert plan_wildcard.policy.generation.max_tokens == 16000

    plan_unmatched = await _plan(org, request_model="other/model")
    assert plan_unmatched.policy.generation.max_tokens == 4096


# --- routing ---------------------------------------------------------------------------


@pytest.mark.setting("routing.mode")
@pytest.mark.setting("routing.fast_model")
async def test_balanced_routing_sends_auxiliary_work_to_the_fast_model() -> None:
    org = "wire_balanced"
    await save_org_config(
        None, org, "routing", {"mode": "balanced", "fast_model": "cheap/fast"}, []
    )

    router = (await _plan(org)).model_router

    assert router.model_for("suggest") == "cheap/fast"
    assert router.model_for("react") == "platform/default"


@pytest.mark.setting("routing.economy_model")
async def test_economy_routing_uses_the_economy_model_for_everything() -> None:
    org = "wire_economy"
    await save_org_config(None, org, "routing", {"mode": "economy", "economy_model": "cheap/e"}, [])

    router = (await _plan(org)).model_router

    assert router.model_for("react") == router.model_for("suggest") == "cheap/e"


# --- budgets ---------------------------------------------------------------------------


def _scope(org: str, user: str = "u1", session: str | None = "s1") -> ModelScope:
    return ModelScope(org_id=org, user_id=user, session_id=session)


async def _spend(
    ledger: UsageLedger, org: str, usd: float, user: str = "u1", session: str = "s1"
) -> None:
    await ledger.record(
        UsageEvent(
            org_id=org,
            user_id=user,
            session_id=session,
            model="m",
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=usd,
        )
    )


@pytest.mark.setting("budgets.monthly_budget_usd")
async def test_the_organization_monthly_cap_stops_spending_for_everyone() -> None:
    org, ledger = "wire_org_cap", UsageLedger()
    await save_org_config(None, org, "budgets", {"monthly_budget_usd": 10.0}, [])
    budget = BudgetService(None, ledger)

    await budget.check(_scope(org))
    await _spend(ledger, org, 10.0, user="someone_else")

    with pytest.raises(BudgetExceededError, match="Organization monthly"):
        await budget.check(_scope(org))


@pytest.mark.setting("budgets.member_monthly_budget_usd")
async def test_a_member_cap_only_stops_that_member() -> None:
    org, ledger = "wire_member_cap", UsageLedger()
    await save_org_config(None, org, "budgets", {"member_monthly_budget_usd": 5.0}, [])
    budget = BudgetService(None, ledger)
    await _spend(ledger, org, 5.0, user="heavy")

    with pytest.raises(BudgetExceededError, match="Your monthly"):
        await budget.check(_scope(org, user="heavy"))
    await budget.check(_scope(org, user="light"))


@pytest.mark.setting("budgets.session_budget_usd")
async def test_a_session_cap_only_stops_that_session() -> None:
    org, ledger = "wire_session_cap", UsageLedger()
    await save_org_config(None, org, "budgets", {"session_budget_usd": 2.0}, [])
    budget = BudgetService(None, ledger)
    await _spend(ledger, org, 2.5, session="expensive")

    with pytest.raises(BudgetExceededError, match="Session"):
        await budget.check(_scope(org, session="expensive"))
    await budget.check(_scope(org, session="fresh"))


@pytest.mark.setting("budgets.warn_at_percent")
async def test_a_cap_close_to_its_limit_warns_before_it_stops() -> None:
    org, ledger = "wire_warn", UsageLedger()
    await save_org_config(
        None, org, "budgets", {"monthly_budget_usd": 10.0, "warn_at_percent": 50}, []
    )
    budget = BudgetService(None, ledger)

    assert (await budget.status(_scope(org))).warnings() == []
    await _spend(ledger, org, 6.0)

    (warning,) = (await budget.status(_scope(org))).warnings()
    assert "60% used" in warning


# --- sign-in ---------------------------------------------------------------------------


@pytest.mark.setting("general.sso_allowed_domains")
@pytest.mark.setting("general.sso_enforce_domain_match")
async def test_sign_in_is_limited_to_the_organizations_domains_only_when_enforced() -> None:
    from api.routers.auth import _sso_allowed

    org = "wire_sso"
    assert await _sso_allowed(None, org, "anyone@elsewhere.test") is True

    await save_org_config(None, org, "general", {"sso_allowed_domains": ["acme.test"]}, [])
    assert await _sso_allowed(None, org, "anyone@elsewhere.test") is True, (
        "listing is not enforcing"
    )

    await save_org_config(
        None,
        org,
        "general",
        {"sso_allowed_domains": ["acme.test"], "sso_enforce_domain_match": True},
        [],
    )
    assert await _sso_allowed(None, org, "alice@acme.test") is True
    assert await _sso_allowed(None, org, "mallory@elsewhere.test") is False


@pytest.mark.setting("general.max_turns")
async def test_the_default_turn_budget_is_the_same_everywhere_it_is_stated() -> None:
    """The registry, the resolver and the orchestrator must not drift apart on the default."""
    from config_engine.registry import REGISTRY

    from specifications.interfaces.agent import DEFAULT_MAX_TURNS

    assert REGISTRY["general"]["max_turns"].default == DEFAULT_MAX_TURNS == 100
    assert (await _plan("wire_default_budget")).max_turns == DEFAULT_MAX_TURNS


@pytest.mark.setting("general.max_turns")
async def test_without_a_step_limit_a_long_turn_runs_to_its_answer_without_wrapping_up() -> None:
    org = "wire_no_step_limit"
    await save_org_config(None, org, "general", {"max_turns": 0}, [])
    script = [("list_dir", {"path": f"dir{i}"}) for i in range(130)]

    outcome = await run_turn(org, script=script, final_text="Finished all 130 steps.")

    react = [r for r in outcome.model.requests if r.tools]
    assert len(react) == 131
    sent = " ".join(m.content or "" for r in react for m in r.messages)
    assert "Budget notice" not in sent and "converging" not in sent
    assert outcome.session.conversation_history[-1].content == "Finished all 130 steps."
