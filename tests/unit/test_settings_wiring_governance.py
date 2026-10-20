"""Governance settings change what a turn does: hooks, model data rules, egress, redaction."""

from __future__ import annotations

import asyncio

import pytest
from api.services.settings import save_org_config
from api.turns.shared import _turn_service
from turn_harness import run_turn

from specifications.interfaces.sandbox import ExecResult

SECRET = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
ECHO = ("bash_exec", {"command": "echo hello", "cwd": "/workspace"})


def _ok(stdout: str, code: int = 0) -> ExecResult:
    return ExecResult(code, stdout, "", 1)


async def _governance(org: str, **values: object) -> None:
    await save_org_config(None, org, "governance", dict(values), [])


@pytest.mark.setting("governance.hooks")
async def test_a_failing_pre_tool_hook_blocks_the_tool_before_it_runs() -> None:
    org = "wire_hooks"
    await _governance(
        org, hooks={"pre_tool": [{"match": "bash_exec", "command": "policy-check.sh"}]}
    )
    from sandbox_driver.fake_driver import FakeSandboxDriver

    driver = FakeSandboxDriver()
    driver.script_command("policy-check.sh", _ok("not allowed here", 1))

    outcome = await run_turn(org, script=[ECHO], driver=driver)

    commands = [command for _, command in driver.exec_calls]
    assert any("policy-check.sh" in c for c in commands)
    assert not any("echo hello" in c for c in commands)
    assert "Blocked by an organization hook" in outcome.text_of_events()


@pytest.mark.setting("governance.allow_bypass_permissions")
async def test_the_bypass_mode_is_honoured_only_where_the_organization_allows_it() -> None:
    async def mode(org: str) -> str:
        plan = await _turn_service.plan_turn(
            db_mgr=None,
            org_id=org,
            user_id="u1",
            agent_id=None,
            global_default_model="p/m",
            session_permission_mode="bypass",
        )
        return plan.policy.permission_mode

    assert await mode("wire_bypass_off") != "bypass"

    await _governance("wire_bypass_on", allow_bypass_permissions=True)
    assert await mode("wire_bypass_on") == "bypass"


ASK = (
    "ask_question",
    {
        "question": "Which environment?",
        "options": ["beta", "qa"],
        "default_recommended_option": "beta",
    },
)


@pytest.mark.setting("governance.question_timeout_seconds")
async def test_an_unanswered_question_closes_after_the_timeout_and_the_turn_finishes() -> None:
    org = "wire_question_timeout"
    await _governance(org, question_timeout_seconds=1)

    outcome = await asyncio.wait_for(run_turn(org, script=[ASK]), timeout=20)

    asked, closed = outcome.events_of("decision_required"), outcome.events_of("decision_submitted")
    assert len(asked) == len(closed) == 1
    assert closed[0].payload["question_id"] == asked[0].payload["question_id"]
    assert closed[0].payload["selected_options"] == []
    tool_results = [
        m.content for r in outcome.model.requests for m in r.messages if m.role == "tool"
    ]
    assert any(
        "Nobody answered within 1 seconds" in str(r) and "recommended option (beta)" in str(r)
        for r in tool_results
    )
    assert outcome.session.conversation_history[-1].content == "All done."


async def test_without_a_timeout_a_question_waits_for_its_answer() -> None:
    turn = asyncio.ensure_future(run_turn("wire_question_waits", script=[ASK]))

    done, _ = await asyncio.wait({turn}, timeout=1.5)
    turn.cancel()

    assert not done, "the turn must still be waiting for an answer"


@pytest.mark.setting("governance.zero_retention")
@pytest.mark.setting("governance.region")
async def test_data_rules_reach_every_model_call_of_the_turn_including_auxiliary_ones() -> None:
    org = "wire_data_rules"
    await _governance(org, zero_retention=True, region="eu")

    outcome = await run_turn(org)

    assert len(outcome.model.requests) >= 2, "the turn makes the agent call and a follow-up call"
    # The gateway applies the scope's rules to every request that does not set them itself.
    assert all(scope.zero_retention and scope.region == "eu" for scope in outcome.model.scopes)


@pytest.mark.setting("governance.provider_allowlist")
async def test_a_model_from_a_provider_outside_the_allowlist_does_not_run() -> None:
    blocked = await run_turn("wire_provider_block", script=[])
    assert blocked.model.requests, "control: with no allowlist the turn reaches the model"

    org = "wire_provider_only"
    await _governance(org, provider_allowlist=["somewhere-else"])
    outcome = await run_turn(org)

    assert outcome.model.requests == []
    assert "not permitted" in outcome.text_of_events().lower() or outcome.events_of("error")


@pytest.mark.setting("governance.egress_allowlist")
@pytest.mark.setting("governance.block_internal_subnets")
async def test_the_egress_rules_reach_the_sandbox_the_turn_creates() -> None:
    org = "wire_egress"
    await _governance(org, egress_allowlist=["203.0.113.0/24"], block_internal_subnets=True)

    outcome = await run_turn(org, script=[ECHO])

    spec = outcome.driver._specs[f"session_{org}"]
    assert spec.egress_allowlist == ["203.0.113.0/24"]
    assert spec.block_internal_subnets is True


async def _leaky_output(org: str) -> str:
    from sandbox_driver.fake_driver import FakeSandboxDriver

    driver = FakeSandboxDriver()
    driver.script_command(
        "echo hello", _ok(f"token {SECRET} mail jane.doe@example.com custom-ID-4711\n")
    )
    return (await run_turn(org, script=[ECHO], driver=driver)).text_of_events()


@pytest.mark.setting("governance.redaction_enabled")
async def test_secrets_are_scrubbed_from_tool_output_unless_redaction_is_off() -> None:
    scrubbed = await _leaky_output("wire_redaction_on")
    assert SECRET not in scrubbed and "[REDACTED" in scrubbed

    org = "wire_redaction_off"
    await _governance(org, redaction_enabled=False)
    assert SECRET in await _leaky_output(org)


@pytest.mark.setting("governance.pii_redaction_enabled")
async def test_email_addresses_are_scrubbed_only_when_pii_redaction_is_on() -> None:
    assert "jane.doe@example.com" in await _leaky_output("wire_pii_off")

    org = "wire_pii_on"
    await _governance(org, pii_redaction_enabled=True)
    assert "jane.doe@example.com" not in await _leaky_output(org)


@pytest.mark.setting("governance.redaction_patterns")
async def test_organization_patterns_are_scrubbed_too() -> None:
    org = "wire_patterns"
    await _governance(org, redaction_patterns=[r"custom-ID-\d+"])

    assert "custom-ID-4711" not in await _leaky_output(org)


# --- retention ---------------------------------------------------------------------------

DAY = 86400
NOW = 1_800_000_000


@pytest.mark.setting("governance.session_retention_days")
@pytest.mark.setting("governance.trace_retention_days")
@pytest.mark.setting("governance.memory_retention_days")
@pytest.mark.setting("governance.attachment_retention_days")
@pytest.mark.setting("governance.audit_retention_days")
@pytest.mark.parametrize(
    ("key", "table"),
    [
        ("session_retention_days", "sessions"),
        ("trace_retention_days", "turn_traces"),
        ("memory_retention_days", "memories"),
        ("attachment_retention_days", "attachments"),
        ("audit_retention_days", "audit_events"),
    ],
)
async def test_each_retention_window_expires_its_own_table_and_no_other(
    key: str, table: str
) -> None:
    from api.bootstrap.background import retention_policy_for
    from api.services.audit import _SWEEP_TABLES

    org = f"wire_{key}"
    await _governance(org, **{key: 30})

    policy = await retention_policy_for(None, org)  # type: ignore[arg-type]

    for swept, _model, _column in _SWEEP_TABLES:
        expected = NOW - 30 * DAY if swept == table else None
        assert policy.cutoff(swept, NOW) == expected, swept
