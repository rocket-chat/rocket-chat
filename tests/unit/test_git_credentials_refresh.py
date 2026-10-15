"""Sandbox git setup is per-turn-safe, idempotent and keeps every GitHub credential out.

The sandbox reaches GitHub through the platform's git gateway. At the start of every turn it
receives a fresh gateway capability as per-command environment (replaced on each injection),
never as a file, dotfile line or git URL, and never a GitHub token in any credential mode.
"""

from __future__ import annotations

import pytest
from api.auth import git_gateway_token
from api.services.git_credentials import GitCredentialBundle, inject_git_credentials_into_sandbox
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult

ORG, USER = "o", "u"


def _driver() -> FakeSandboxDriver:
    driver = FakeSandboxDriver()
    driver.script_command("git config", ExecResult(0, "", "", 0))
    return driver


def _bundle(token: str | None) -> GitCredentialBundle:
    return GitCredentialBundle(
        token=token,
        mode="app" if token else "public",
        author_name="Rocket Bot",
        author_email="bot@rocket-chat.internal",
    )


async def _inject(driver: FakeSandboxDriver, bundle: GitCredentialBundle) -> None:
    await inject_git_credentials_into_sandbox(
        driver, "sess", bundle, org_id=ORG, user_id=USER, api_url="https://api.github.com"
    )


def _capability(driver: FakeSandboxDriver) -> str:
    env = driver.merged_env("sess", None)
    header = env[f"GIT_CONFIG_VALUE_{int(env['GIT_CONFIG_COUNT']) - 1}"]
    return header.split(": ", 1)[1]


@pytest.mark.asyncio
async def test_reinjection_replaces_the_capability_instead_of_accumulating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _driver()
    monkeypatch.setattr(git_gateway_token.time, "time", lambda: 1_000_000.0)
    await _inject(driver, _bundle("ghs_first"))
    first = _capability(driver)
    monkeypatch.setattr(git_gateway_token.time, "time", lambda: 1_000_600.0)
    await _inject(driver, _bundle("ghs_second"))

    assert _capability(driver) != first, "a fresh capability every turn"
    assert len([k for k in driver.merged_env("sess", None) if k.startswith("GIT_CONFIG_KEY")]) == 4
    # The setup script is identical both times: nothing credential-dependent was written.
    assert driver.exec_calls[0][1] == driver.exec_calls[1][1]


@pytest.mark.asyncio
async def test_no_credential_is_ever_written_into_the_sandbox() -> None:
    driver = _driver()
    await _inject(driver, _bundle("ghs_token"))

    script = driver.exec_calls[-1][1]
    env = driver.merged_env("sess", None)
    assert "ghs_token" not in script and "ghs_token" not in str(env)
    assert _capability(driver) not in script, "the capability is environment, never a file"
    assert ">> " not in script and "> /root/.git-credentials" not in script


@pytest.mark.asyncio
async def test_legacy_credentials_are_cleared() -> None:
    driver = _driver()
    await _inject(driver, _bundle(None))

    script = driver.exec_calls[-1][1]
    assert "--unset-all credential.helper" in script
    # Secrets earlier versions persisted on long-lived volumes are removed.
    assert "sed -i '/^export GITHUB_TOKEN=/d;/^export GH_TOKEN=/d'" in script
    assert "/etc/environment" in script and ".git-credentials" in script
