"""Git credential injection is per-turn-safe, idempotent and keeps secrets out of the sandbox.

GitHub App installation tokens expire after ~1 hour, so credentials are re-injected at the
start of every turn. The token travels as per-command environment (replaced on each
injection), never as a file, dotfile line or git URL, so a long-lived sandbox accumulates
nothing and a stopped one holds no secret.
"""

from __future__ import annotations

import pytest
from api.services.git_credentials import GitCredentialBundle, inject_git_credentials_into_sandbox
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult


def _driver() -> FakeSandboxDriver:
    driver = FakeSandboxDriver()
    driver.script_command("git config", ExecResult(0, "", "", 0))
    return driver


def _bundle(token: str | None) -> GitCredentialBundle:
    return GitCredentialBundle(
        token=token,
        mode="app_readonly" if token else "none",
        author_name="Rocket Bot",
        author_email="bot@rocket-chat.internal",
    )


@pytest.mark.asyncio
async def test_reinjection_replaces_the_token_instead_of_accumulating() -> None:
    driver = _driver()
    await inject_git_credentials_into_sandbox(driver, "sess", _bundle("ghs_first"))
    await inject_git_credentials_into_sandbox(driver, "sess", _bundle("ghs_second"))

    assert driver.merged_env("sess", None)["GITHUB_TOKEN"] == "ghs_second"
    assert driver.merged_env("sess", None)["GH_TOKEN"] == "ghs_second"
    # The setup script is identical both times: nothing token-dependent was written.
    assert driver.exec_calls[0][1] == driver.exec_calls[1][1]


@pytest.mark.asyncio
async def test_the_token_is_never_written_into_the_sandbox() -> None:
    driver = _driver()
    await inject_git_credentials_into_sandbox(driver, "sess", _bundle("ghs_token"))

    script = driver.exec_calls[-1][1]
    assert "ghs_token" not in script
    assert ">> " not in script and "> /root/.git-credentials" not in script
    assert "x-access-token:ghs_token" not in script
    # git is pointed at a helper that reads the environment of the command that needs it.
    assert "credential.helper" in script and "${GITHUB_TOKEN:-$GH_TOKEN}" in script


@pytest.mark.asyncio
async def test_unauthenticated_bundle_clears_credentials_and_legacy_leftovers() -> None:
    driver = _driver()
    await inject_git_credentials_into_sandbox(driver, "sess", _bundle("ghs_old"))
    await inject_git_credentials_into_sandbox(driver, "sess", _bundle(None))

    assert "GITHUB_TOKEN" not in driver.merged_env("sess", None)
    script = driver.exec_calls[-1][1]
    assert "--unset-all credential.helper" in script
    # Secrets earlier versions persisted on long-lived volumes are removed.
    assert "sed -i '/^export GITHUB_TOKEN=/d;/^export GH_TOKEN=/d'" in script
    assert "/etc/environment" in script and ".git-credentials" in script
