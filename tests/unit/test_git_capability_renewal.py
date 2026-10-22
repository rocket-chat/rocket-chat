"""The sandbox's git gateway capability outlives a turn that outlives its hour.

Three layers, tested together because each covers what the others cannot: the driver asks for a
renewal before every command, the gateway still serves an expired token while its session has a
turn running (a background process keeps the environment it was born with), and a refusal that
remains is reported as a refused credential, never as a missing branch.
"""

from __future__ import annotations

import pytest
from api.auth import git_gateway_token
from api.services.git_credentials import GitCredentialBundle, inject_git_credentials_into_sandbox
from api.services.git_gateway import EXPIRED_MESSAGE, GatewayRefusal, GitGateway
from api.store import InMemorySessionStore
from git_engine.publish import PublishError, _Sandbox
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult
from specifications.interfaces.session import SessionRecord

ORG, USER, SESSION = "org_cap", "user_cap", "sess"
HOUR = git_gateway_token.GIT_GATEWAY_TTL_SECONDS
START = 2_000_000.0


def _capability(driver: FakeSandboxDriver) -> str:
    env = driver.merged_env(SESSION, None)
    return env[f"GIT_CONFIG_VALUE_{int(env['GIT_CONFIG_COUNT']) - 1}"].split(": ", 1)[1]


async def _inject(driver: FakeSandboxDriver) -> None:
    bundle = GitCredentialBundle(
        token=None, mode="public", author_name="Bot", author_email="bot@example.org"
    )
    await inject_git_credentials_into_sandbox(
        driver, SESSION, bundle, org_id=ORG, user_id=USER, api_url="https://api.github.com"
    )


def test_an_expired_token_is_distinguished_from_a_forged_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = git_gateway_token.mint(SESSION, ORG, USER, now=START)

    assert git_gateway_token.verify(token, now=START + 10).session_id == SESSION
    with pytest.raises(git_gateway_token.GatewayCredentialExpired) as expired:
        git_gateway_token.verify(token, now=START + HOUR + 1)
    assert expired.value.grant.session_id == SESSION
    with pytest.raises(ValueError) as too_old:
        git_gateway_token.verify(
            token, now=START + HOUR + git_gateway_token.EXPIRED_GRACE_SECONDS + 1
        )
    assert not isinstance(too_old.value, git_gateway_token.GatewayCredentialExpired)
    with pytest.raises(ValueError):
        git_gateway_token.verify(token[:-4] + "AAAA", now=START)


@pytest.mark.asyncio
async def test_the_driver_gets_a_new_capability_once_less_than_twenty_minutes_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = FakeSandboxDriver()
    driver.script_command("git config", ExecResult(0, "", "", 0))
    clock = {"now": START}
    monkeypatch.setattr(git_gateway_token.time, "time", lambda: clock["now"])
    await _inject(driver)
    first = _capability(driver)

    clock["now"] = START + HOUR - git_gateway_token.RENEW_BEFORE_SECONDS - 60
    assert _capability(driver) == first, "plenty of time left: the same capability"

    clock["now"] = START + HOUR - git_gateway_token.RENEW_BEFORE_SECONDS + 60
    renewed = _capability(driver)
    assert renewed != first
    grant = git_gateway_token.verify(renewed, now=clock["now"])
    assert grant.expires_at == int(clock["now"]) + HOUR
    assert _capability(driver) == renewed, "renewed once, not on every command"


@pytest.mark.asyncio
async def test_an_expired_capability_is_served_only_while_the_session_has_a_turn_running() -> None:
    store = InMemorySessionStore()
    await store.create_session(
        SessionRecord(session_id=SESSION, tenant_org_id=ORG, tenant_user_id=USER, title="t")
    )
    gateway = GitGateway(db_mgr=None, store=store, git_engine=None)
    token = git_gateway_token.mint(SESSION, ORG, USER, now=START)
    with pytest.raises(git_gateway_token.GatewayCredentialExpired) as expired:
        git_gateway_token.verify(token, now=START + HOUR + 5)
    grant = expired.value.grant

    with pytest.raises(GatewayRefusal) as idle:
        await gateway.authorize(grant, expired=True)
    assert idle.value.status == 401 and str(idle.value) == EXPIRED_MESSAGE

    record = await store.get_session(SESSION, ORG)
    assert record is not None
    record.is_processing = True
    await store.update_session(record)
    await gateway.authorize(grant, expired=True)


def _sandbox_refused_with(stderr: str) -> tuple[_Sandbox, FakeSandboxDriver]:
    driver = FakeSandboxDriver()
    driver.script_command("git fetch", ExecResult(128, "", stderr, 0))
    return _Sandbox(driver, SESSION, "/workspace/app"), driver


@pytest.mark.asyncio
async def test_a_refused_credential_is_not_reported_as_a_missing_branch() -> None:
    sandbox, driver = _sandbox_refused_with(f"fatal: unable to access: {EXPIRED_MESSAGE}")

    with pytest.raises(PublishError, match="credential for the platform was refused"):
        await sandbox.fetch_branch("master", required=False)
    fetches = [c for _, c in driver.exec_calls if "git fetch" in c]
    assert len(fetches) == 2, "one more try, since the credential is renewed per command"


@pytest.mark.asyncio
async def test_a_branch_that_is_really_missing_is_still_just_missing() -> None:
    sandbox, _ = _sandbox_refused_with("fatal: couldn't find remote ref refs/heads/nope")

    assert await sandbox.fetch_branch("nope", required=False) is False
