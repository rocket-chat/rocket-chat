"""The one rule that resolves a setting: policy x lock x preset x ceiling (P2.1, P2.2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from config_engine import Preset, ResolveContext, resolve
from config_engine.exceptions import PolicyViolationError
from config_engine.policy import Policy, quantity
from config_engine.registry import REGISTRY, instance_values, validate, writable_keys


@dataclass(frozen=True)
class Spec:
    key: str = "x"
    default: Any = "platform"
    policy: Policy = "org_default"
    ceiling: bool = False
    member_override: bool = True


AGENT = Preset("agent:a1", {"x": "agent"})
SESSION = Preset("session", {"x": "session"})


@pytest.mark.parametrize(
    ("policy", "locked", "presets", "member", "expected", "source", "locked_by"),
    [
        # org_default: presets beat the member, the member beats the organization.
        ("org_default", False, (SESSION, AGENT), "member", "session", "session", None),
        ("org_default", False, (AGENT,), "member", "agent", "agent:a1", None),
        ("org_default", False, (), "member", "member", "member", None),
        ("org_default", False, (), None, "org", "org", None),
        # A lock keeps the organization's value whatever a preset or the member says.
        ("org_default", True, (SESSION, AGENT), "member", "org", "org", "org"),
        # org: administrators decide; presets and members are ignored.
        ("org", False, (AGENT,), "member", "org", "org", None),
        # member: the organization's value never applies.
        ("member", False, (), "member", "member", "member", None),
        ("member", False, (), None, "platform", "platform", None),
        # instance: the operator's value, locked against everyone.
        ("instance", False, (AGENT,), "member", "platform", "platform", "instance"),
    ],
)
def test_policy_lock_and_presets(
    policy: Policy,
    locked: bool,
    presets: tuple[Preset, ...],
    member: str | None,
    expected: str,
    source: str,
    locked_by: str | None,
) -> None:
    ctx = ResolveContext(
        org={"x": "org"},
        locked=frozenset({"x"}) if locked else frozenset(),
        member={"x": member} if member else {},
        presets=presets,
    )
    resolved = resolve(Spec(policy=policy), ctx)
    assert (resolved.value, resolved.source, resolved.locked_by) == (expected, source, locked_by)


def test_a_lock_reports_the_choices_it_refused() -> None:
    ctx = ResolveContext(org={"x": "org"}, locked=frozenset({"x"}), presets=(AGENT,))
    assert resolve(Spec(), ctx).refused == ("agent:a1",)


def test_the_operator_value_sits_under_the_organization() -> None:
    spec = Spec(policy="org")
    assert resolve(spec, ResolveContext(instance={"x": "env"})).source == "instance"
    assert resolve(spec, ResolveContext(org={"x": "org"}, instance={"x": "env"})).value == "org"


@pytest.mark.parametrize(
    ("org", "agent", "expected", "source"),
    [
        (100, 30, 30, "agent:a1"),  # lower: accepted
        (100, 300, 100, "org"),  # higher: refused, the ceiling applies
        (100, 0, 100, "org"),  # 0 means unlimited: above any finite ceiling
        (0, 300, 300, "agent:a1"),  # an unlimited organization lets the agent choose
        (0, 0, 0, "agent:a1"),
    ],
)
def test_a_ceiling_only_goes_down(org: int, agent: int, expected: int, source: str) -> None:
    spec = Spec(key="max_turns", default=100, policy="org", ceiling=True)
    ctx = ResolveContext(
        org={"max_turns": org}, presets=(Preset("agent:a1", {"max_turns": agent}),)
    )
    resolved = resolve(spec, ctx)
    assert (resolved.value, resolved.source) == (expected, source)


def test_a_ceiling_compares_quantities() -> None:
    assert quantity("4Gi") == 4 * 2**30
    assert quantity("500m") == 0.5
    spec = Spec(key="memory", default="4Gi", policy="org", ceiling=True)
    ctx = ResolveContext(org={"memory": "4Gi"}, presets=(Preset("agent:a1", {"memory": "8Gi"}),))
    assert resolve(spec, ctx).value == "4Gi"
    ctx = ResolveContext(org={"memory": "4Gi"}, presets=(Preset("agent:a1", {"memory": "512Mi"}),))
    assert resolve(spec, ctx).value == "512Mi"


def test_the_acceptance_test_skips_refused_candidates() -> None:
    ctx = ResolveContext(
        org={"x": "forbidden-org"},
        presets=(Preset("agent:a1", {"x": "forbidden"}),),
        accept=lambda value: not str(value).startswith("forbidden"),
    )
    resolved = resolve(Spec(), ctx)
    assert (resolved.value, resolved.source) == ("platform", "platform")
    assert resolved.refused == ("agent:a1", "org")


def test_nothing_acceptable_is_a_policy_violation() -> None:
    with pytest.raises(PolicyViolationError):
        resolve(Spec(default="no"), ResolveContext(accept=lambda value: False))


def test_a_preset_only_key_ignores_the_member() -> None:
    spec = Spec(member_override=False)
    ctx = ResolveContext(
        org={"x": "org"}, member={"x": "member"}, presets=(Preset("slack_bot:b", {"x": "bot"}),)
    )
    assert resolve(spec, ctx).value == "bot"
    assert resolve(spec, ResolveContext(org={"x": "org"}, member={"x": "member"})).value == "org"


def test_registry_writers_follow_the_policy() -> None:
    assert "permission_mode" in writable_keys("user_preferences", "member")
    assert "permission_mode" in writable_keys("user_preferences", "org")
    assert "full_name" not in writable_keys("user_preferences", "org")
    # Slack keys a bot overrides are organization defaults no member may set.
    assert REGISTRY["slack"]["dm_policy"].policy == "org_default"
    assert "dm_policy" not in writable_keys("slack", "member")
    rejected = validate("slack", {"dm_policy": "all"}, writer="member")
    assert rejected.errors and "administrator" in rejected.errors[0].message


def test_operator_values_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MAX_TURNS", "42")
    monkeypatch.setenv("GITHUB_APP_DEFAULT_PERMISSIONS", "contents:read,checks:read")
    assert instance_values("general")["max_turns"] == 42
    assert instance_values("github")["app_token_permissions"] == {
        "contents": "read",
        "checks": "read",
    }
    monkeypatch.setenv("AGENT_MAX_TURNS", "not-a-number")
    assert "max_turns" not in instance_values("general")


def test_list_choices_are_validated() -> None:
    ok = validate("access", {"bring_own_keys": ["admin", "member"]}, writer="org")
    assert ok.ok
    bad = validate("access", {"bring_own_keys": ["superuser"]}, writer="org")
    assert not bad.ok
