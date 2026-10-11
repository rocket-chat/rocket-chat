"""Unit tests for user credential encryption, storage, and Git credential resolution."""

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from api.services.credentials import (
    decrypt_secret,
    delete_user_credential,
    encrypt_secret,
    get_user_credential,
    store_user_credential,
)
from api.services.git_credentials import (
    EXPIRED_LINK_WARNING,
    GitCredentialBundle,
    expiring_token_metadata,
    github_token_is_valid,
    inject_git_credentials_into_sandbox,
    refresh_github_user_token,
    resolve_session_git_credentials,
)
from git_engine.config import GitEngineSettings
from git_engine.engine import GitEngine
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult


def test_encryption_roundtrip() -> None:
    plaintext = "ghp_secure_github_personal_token_xyz"
    encrypted = encrypt_secret(plaintext)
    assert encrypted != plaintext
    assert decrypt_secret(encrypted) == plaintext


def test_decryption_invalid_ciphertext() -> None:
    with pytest.raises(ValueError):
        decrypt_secret("invalid-corrupted-ciphertext")


@pytest.mark.asyncio
async def test_store_and_get_user_credential_in_memory() -> None:
    user_id = "test_user_42"
    org_id = "test_org_alpha"
    token = "gho_oauth_access_token_123"
    meta = {"login": "octocat", "scopes": ["repo", "read:user"]}

    stored = await store_user_credential(
        db_mgr=None,
        user_id=user_id,
        org_id=org_id,
        provider="github",
        secret=token,
        metadata=meta,
    )

    assert stored.user_id == user_id
    assert stored.secret == token
    assert stored.metadata["login"] == "octocat"

    retrieved = await get_user_credential(db_mgr=None, user_id=user_id, provider="github")
    assert retrieved is not None
    assert retrieved.secret == token
    assert retrieved.metadata["scopes"] == ["repo", "read:user"]

    deleted = await delete_user_credential(db_mgr=None, user_id=user_id, provider="github")
    assert deleted is True

    after_delete = await get_user_credential(db_mgr=None, user_id=user_id, provider="github")
    assert after_delete is None


@pytest.mark.asyncio
async def test_resolve_git_credentials_priority_user_token() -> None:
    user_id = "pilot_dev"
    org_id = "space_agency"
    token = "ghp_custom_user_pat"
    await store_user_credential(
        db_mgr=None,
        user_id=user_id,
        org_id=org_id,
        provider="github",
        secret=token,
        metadata={"login": "astro-dev", "name": "Major Tom", "scopes": ["repo"]},
    )

    bundle = await resolve_session_git_credentials(
        user_id=user_id,
        org_id=org_id,
        git_engine=None,
        db_mgr=None,
    )

    assert bundle.mode == "user_token"
    assert bundle.token == token
    assert bundle.author_name == "Major Tom"
    assert bundle.source_details["username"] == "astro-dev"


@pytest.mark.asyncio
async def test_resolve_git_credentials_priority_app_readonly() -> None:
    user_id = "unlinked_user"
    org_id = "space_agency"
    # Ensure unlinked
    await delete_user_credential(db_mgr=None, user_id=user_id, provider="github")

    settings = GitEngineSettings(
        github_app_id="app-123",
        github_app_private_key="dummy-key",
        bot_author_name="Rocket Bot",
        bot_author_email="bot@rocket.test",
    )
    git_engine = GitEngine(settings=settings)
    git_engine.get_github_access_token = AsyncMock(return_value="ghs_temp_installation_token_abc")  # type: ignore[method-assign]

    bundle = await resolve_session_git_credentials(
        user_id=user_id,
        org_id=org_id,
        git_engine=git_engine,
        db_mgr=None,
    )

    assert bundle.mode == "app_readonly"
    assert bundle.token == "ghs_temp_installation_token_abc"
    assert bundle.author_name == "Rocket Bot"
    assert bundle.author_email == "bot@rocket.test"


@pytest.mark.asyncio
async def test_resolve_git_credentials_fallback_none() -> None:
    user_id = "no_auth_user"
    org_id = "space_agency"
    await delete_user_credential(db_mgr=None, user_id=user_id, provider="github")

    settings = GitEngineSettings()  # No app ID or private key
    git_engine = GitEngine(settings=settings)

    bundle = await resolve_session_git_credentials(
        user_id=user_id,
        org_id=org_id,
        git_engine=git_engine,
        db_mgr=None,
        user_overrides={"git_author_name": "Custom Operator"},
    )

    assert bundle.mode == "none"
    assert bundle.token is None
    assert bundle.author_name == "Custom Operator"


@pytest.mark.asyncio
async def test_inject_git_credentials_into_sandbox() -> None:
    driver = FakeSandboxDriver()
    driver.script_command(
        "git config", ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1)
    )
    bundle = GitCredentialBundle(
        token="gho_test_token",
        mode="user_token",
        author_name="Alice Developer",
        author_email="alice@company.com",
    )

    await inject_git_credentials_into_sandbox(driver, "session_xyz", bundle)

    script = driver.exec_calls[-1][1]
    assert "user.name 'Alice Developer'" in script
    assert "user.email alice@company.com" in script or "user.email 'alice@company.com'" in script
    # Identity and a token-less helper go into git config...
    assert "credential.helper" in script and "GITHUB_TOKEN" in script
    # ...but the secret never touches the sandbox's disk, dotfiles or git URLs.
    assert "gho_test_token" not in script
    assert "store --file" not in script  # no on-disk credential store
    assert "insteadOf" not in script
    # It travels as per-command environment instead.
    assert driver.merged_env("session_xyz", None) == {
        "GITHUB_TOKEN": "gho_test_token",
        "GH_TOKEN": "gho_test_token",
        "GIT_TERMINAL_PROMPT": "0",
    }


@pytest.mark.asyncio
async def test_a_revoked_token_is_removed_from_the_session_environment() -> None:
    driver = FakeSandboxDriver()
    driver.script_command(
        "git config", ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1)
    )
    linked = GitCredentialBundle(
        token="t", mode="user_token", author_name="A", author_email="a@x.io"
    )
    await inject_git_credentials_into_sandbox(driver, "s", linked)
    unlinked = GitCredentialBundle(token=None, mode="none", author_name="A", author_email="a@x.io")
    await inject_git_credentials_into_sandbox(driver, "s", unlinked)

    assert "GITHUB_TOKEN" not in driver.merged_env("s", None)
    assert "--unset-all credential.helper" in driver.exec_calls[-1][1]


_REAL_CLIENT = httpx.AsyncClient


def _github_answers(
    monkeypatch: pytest.MonkeyPatch, status: int | Exception
) -> list[httpx.Request]:
    """Make every httpx client in the service talk to a stub GitHub; returns the requests seen."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(status, Exception):
            raise status
        return httpx.Response(status, json={"login": "octocat"})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
    )
    return seen


@pytest.mark.asyncio
async def test_a_token_github_rejects_is_invalid_and_a_good_one_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _github_answers(monkeypatch, 401)
    assert await github_token_is_valid("ghu_expired_" + "x" * 20) is False
    _github_answers(monkeypatch, 200)
    assert await github_token_is_valid("ghu_fine_" + "x" * 20) is True


@pytest.mark.asyncio
async def test_a_network_failure_does_not_disable_a_good_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _github_answers(monkeypatch, httpx.ConnectError("offline"))
    assert await github_token_is_valid("ghu_offline_" + "x" * 20) is True


@pytest.mark.asyncio
async def test_the_verdict_is_cached_and_never_stores_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import git_credentials

    seen = _github_answers(monkeypatch, 200)
    token = "ghu_cached_" + "x" * 20
    assert await github_token_is_valid(token) is True
    assert await github_token_is_valid(token) is True

    assert len(seen) == 1
    assert token not in repr(git_credentials._token_verdicts)


@pytest.mark.asyncio
async def test_an_expired_link_does_not_beat_the_app_and_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, org_id = "expired_user", "expired_org"
    await store_user_credential(
        db_mgr=None, user_id=user_id, org_id=org_id, provider="github",
        secret="ghu_" + "e" * 36, metadata={"login": "someone", "auth_type": "oauth"},
    )  # fmt: skip

    async def rejected(token: str, api_url: str = "https://api.github.com") -> bool:
        return False

    monkeypatch.setattr("api.services.git_credentials.github_token_is_valid", rejected)
    engine = GitEngine(settings=GitEngineSettings(), driver=FakeSandboxDriver())
    engine.get_github_access_token = AsyncMock(return_value="ghs_app_installation_token")  # type: ignore[method-assign]

    bundle = await resolve_session_git_credentials(
        user_id=user_id, org_id=org_id, git_engine=engine, db_mgr=None, repo="acme/private"
    )

    assert bundle.mode == "app_readonly"
    assert bundle.token == "ghs_app_installation_token"
    assert bundle.warning == EXPIRED_LINK_WARNING


@pytest.mark.asyncio
async def test_an_expired_link_with_no_app_yields_no_token_not_a_dead_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, org_id = "expired_user_2", "expired_org_2"
    await store_user_credential(
        db_mgr=None, user_id=user_id, org_id=org_id, provider="github",
        secret="ghu_" + "d" * 36, metadata={"login": "someone"},
    )  # fmt: skip

    async def rejected(token: str, api_url: str = "https://api.github.com") -> bool:
        return False

    monkeypatch.setattr("api.services.git_credentials.github_token_is_valid", rejected)

    bundle = await resolve_session_git_credentials(
        user_id=user_id, org_id=org_id, git_engine=None, db_mgr=None
    )

    assert bundle.mode == "none" and bundle.token is None
    assert bundle.warning == EXPIRED_LINK_WARNING


def test_expiry_and_refresh_token_are_remembered_and_the_refresh_token_is_encrypted() -> None:
    meta = expiring_token_metadata(
        {"access_token": "ghu_a", "expires_in": 28800, "refresh_token": "ghr_secret_refresh"}
    )

    assert meta["expires_at"] > time.time() + 28000
    assert "ghr_secret_refresh" not in repr(meta)
    assert decrypt_secret(meta["refresh_token_enc"]) == "ghr_secret_refresh"
    assert expiring_token_metadata({"access_token": "gho_classic"}) == {}


async def _expired_link(user_id: str, *, refresh: str | None = "ghr_old") -> None:
    meta: dict[str, Any] = {
        "login": "octocat",
        "auth_type": "oauth",
        "expires_at": int(time.time()) - 60,
    }
    if refresh:
        meta["refresh_token_enc"] = encrypt_secret(refresh)
    await store_user_credential(
        db_mgr=None, user_id=user_id, org_id="org_r", provider="github",
        secret="ghu_old_access", metadata=meta,
    )  # fmt: skip


@pytest.mark.asyncio
async def test_an_expired_github_app_token_is_renewed_and_the_new_pair_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "client-secret")
    await _expired_link("renew_user")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "ghu_new_access",
                "expires_in": 28800,
                "refresh_token": "ghr_new",
            },
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw),
    )
    current = await get_user_credential(None, "renew_user", "github")
    assert current is not None

    renewed = await refresh_github_user_token(None, current)

    assert renewed.secret == "ghu_new_access"
    assert decrypt_secret(renewed.metadata["refresh_token_enc"]) == "ghr_new"
    assert renewed.metadata["login"] == "octocat", "the rest of the link is kept"
    body = seen[0].content.decode()
    assert "grant_type=refresh_token" in body and "refresh_token=ghr_old" in body
    stored = await get_user_credential(None, "renew_user", "github")
    assert stored is not None and stored.secret == "ghu_new_access"


@pytest.mark.asyncio
async def test_concurrent_turns_renew_once_because_refresh_tokens_are_single_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "client-secret")
    await _expired_link("race_user")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json={"access_token": "ghu_once", "expires_in": 28800, "refresh_token": "ghr_2"}
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw),
    )
    current = await get_user_credential(None, "race_user", "github")
    assert current is not None

    results = await asyncio.gather(*(refresh_github_user_token(None, current) for _ in range(4)))

    assert calls == 1
    assert {r.secret for r in results} == {"ghu_once"}


@pytest.mark.asyncio
async def test_when_github_refuses_the_renewal_the_credential_is_left_for_the_validity_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "client-secret")
    await _expired_link("refused_user")
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: _REAL_CLIENT(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"error": "bad_refresh_token"})
            ),
            **kw,
        ),
    )
    current = await get_user_credential(None, "refused_user", "github")
    assert current is not None

    assert (await refresh_github_user_token(None, current)).secret == "ghu_old_access"


@pytest.mark.asyncio
async def test_a_token_without_expiry_or_with_time_left_is_never_refreshed() -> None:
    await store_user_credential(
        None, "classic_user", "org_r", "github", "gho_classic", {"auth_type": "oauth"}
    )
    classic = await get_user_credential(None, "classic_user", "github")
    assert classic is not None
    assert await refresh_github_user_token(None, classic) is classic

    await store_user_credential(
        None, "fresh_user", "org_r", "github", "ghu_fresh",
        {"expires_at": int(time.time()) + 3600, "refresh_token_enc": encrypt_secret("ghr_x")},
    )  # fmt: skip
    fresh = await get_user_credential(None, "fresh_user", "github")
    assert fresh is not None
    assert await refresh_github_user_token(None, fresh) is fresh
