"""Deployment variables are defaults below the settings, and retired aliases still work."""

from __future__ import annotations

import logging

import pytest
from config_engine.environment import _warned, env_value
from sandbox_driver.k8s_driver import _sizing

from specifications.interfaces.sandbox import ResourceLimits, WorkspaceSpec


def _spec(**resources: object) -> WorkspaceSpec:
    return WorkspaceSpec(
        session_id="s",
        tenant_org_id="o",
        tenant_user_id="u",
        container_image="img",
        resources=ResourceLimits(**resources),  # type: ignore[arg-type]
    )


def test_the_sessions_resources_win_over_the_deployment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_CPU_LIMIT", "9")
    monkeypatch.setenv("SANDBOX_MEMORY_LIMIT", "99Gi")

    limits, _ = _sizing(_spec(cpu_cores=3.0, memory_limit="6Gi"), None)

    assert limits == {"cpu": "3.0", "memory": "6Gi"}


def test_the_deployment_variables_fill_in_what_the_session_leaves_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_CPU_LIMIT", "750m")
    monkeypatch.setenv("SANDBOX_MEMORY_LIMIT", "1Gi")

    limits, _ = _sizing(_spec(cpu_cores=0, memory_limit=""), None)

    assert limits == {"cpu": "750m", "memory": "1Gi"}


def test_the_documented_name_wins_and_an_alias_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _warned.discard("OLD_NAME")
    monkeypatch.delenv("NEW_NAME", raising=False)
    monkeypatch.setenv("OLD_NAME", "legacy")
    with caplog.at_level(logging.WARNING):
        assert env_value("NEW_NAME", "OLD_NAME") == "legacy"
        assert env_value("NEW_NAME", "OLD_NAME") == "legacy"
    assert caplog.text.count("OLD_NAME is deprecated; use NEW_NAME") == 1

    monkeypatch.setenv("NEW_NAME", "current")
    assert env_value("NEW_NAME", "OLD_NAME") == "current"
    monkeypatch.delenv("NEW_NAME")
    monkeypatch.delenv("OLD_NAME")
    assert env_value("NEW_NAME", "OLD_NAME") is None
