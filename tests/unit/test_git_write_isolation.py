"""Commits leave the sandbox only through ``git_push``: what the sandbox is given, and the guard."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest
from api.services import git_credentials
from api.services.credentials import store_user_credential
from api.services.git_credentials import (
    inject_git_credentials_into_sandbox,
    resolve_session_git_credentials,
)
from api.services.git_signing import MemberSigning, gpg_available, inspect_private_key

from tests.support.gpg_keys import make_key
from tests.support.local_git_driver import LocalGitDriver

USER = "isolation_user"
ORG = "org-iso"


class _Engine:
    """A GitHub App that can mint a read-only token for the repository it is installed on."""

    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            github_api_url="https://api.github.com",
            bot_author_name="RocketChat Bot",
            bot_author_email="bot@rocketchat.internal",
        )
        self.requests: list[dict[str, Any]] = []

    async def get_github_access_token(self, **kwargs: Any) -> str | None:
        self.requests.append(kwargs)
        return "ghs_app_readonly" if kwargs.get("prefer_app") else "deployment-token"


@pytest.fixture(autouse=True)
def github_is_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def valid(*_: Any, **__: Any) -> bool:
        return True

    async def unchanged(_db: Any, credential: Any) -> Any:
        return credential

    async def no_installations(*_: Any, **__: Any) -> list[Any]:
        return []

    monkeypatch.setattr(git_credentials, "github_token_is_valid", valid)
    monkeypatch.setattr(git_credentials, "refresh_github_user_token", unchanged)
    monkeypatch.setattr(git_credentials, "installations_for_org", no_installations)
    monkeypatch.setattr(git_credentials, "with_github_identity", _identity_unchanged)


async def _identity_unchanged(_db: Any, credential: Any, _api: str) -> Any:
    return credential


async def _link() -> None:
    await store_user_credential(None, USER, ORG, "github", "gho_member", {"login": "octocat"})


async def test_the_sandbox_gets_the_apps_read_only_token_and_the_member_token_stays_host_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _link()

    async def readable(*_: Any) -> bool:
        return True

    monkeypatch.setattr(git_credentials, "_token_reads_repo", readable)
    engine = _Engine()

    bundle = await resolve_session_git_credentials(
        USER,
        ORG,
        engine,
        None,
        {},
        repo="acme/widgets",  # type: ignore[arg-type]
    )

    assert bundle.mode == "app_readonly" and bundle.token == "ghs_app_readonly"
    assert bundle.user_token == "gho_member", "kept for git_push and pull requests"
    assert bundle.author_name == "octocat", "commits are still made as the member"
    assert engine.requests[0]["prefer_app"] is True and len(engine.requests) == 1, (
        "a deployment-wide token never stands in for the App once the member has linked GitHub"
    )


async def test_a_repository_the_app_cannot_see_falls_back_to_the_members_own_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _link()

    async def invisible(*_: Any) -> bool:
        return False

    monkeypatch.setattr(git_credentials, "_token_reads_repo", invisible)

    bundle = await resolve_session_git_credentials(
        USER,
        ORG,
        _Engine(),
        None,
        {},
        repo="acme/private",  # type: ignore[arg-type]
    )

    assert bundle.mode == "user_token" and bundle.token == "gho_member"


async def test_a_member_with_nothing_linked_still_reads_with_the_deployments_token() -> None:
    bundle = await resolve_session_git_credentials(
        "unlinked_user",
        ORG,
        _Engine(),
        None,
        {},  # type: ignore[arg-type]
    )

    assert bundle.mode == "app_readonly" and bundle.token == "ghs_app_readonly"
    assert bundle.author_name == "RocketChat Bot" and bundle.user_token is None


def _remote_and_work(tmp: pathlib.Path) -> pathlib.Path:
    bare = tmp / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    work = tmp / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    for key, value in (("user.name", "T"), ("user.email", "t@example.com")):
        subprocess.run(["git", "-C", str(work), "config", key, value], check=True)
    (work / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "first"], check=True)
    return work


async def test_a_raw_git_push_is_refused_with_the_way_forward_and_the_publisher_may_push(
    tmp_path: pathlib.Path,
) -> None:
    work = _remote_and_work(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    driver = LocalGitDriver(work, tmp_path / "shim")
    driver.home = home
    bundle = git_credentials.GitCredentialBundle(
        token="ghs_app_readonly",
        mode="app_readonly",
        author_name="octocat",
        author_email="o@x.test",
    )

    await inject_git_credentials_into_sandbox(driver, "s1", bundle)  # type: ignore[arg-type]

    refused = await driver.exec_command("s1", "git push origin HEAD:refs/heads/x")
    assert refused.exit_code != 0 and "git_push tool" in refused.stderr
    allowed = await driver.exec_command(
        "s1", "git push origin HEAD:refs/heads/x", env={"ROCKET_PUBLISH": "1"}
    )
    assert allowed.exit_code == 0, allowed.stderr
    assert "ghs_app_readonly" == driver.session_env["GITHUB_TOKEN"]


async def test_a_commit_made_in_the_sandbox_is_signed_with_the_members_key_until_the_turn_ends(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not gpg_available():
        pytest.skip("needs GnuPG")
    private, _ = make_key(tmp_path, "Octo Cat <octo@example.com>", passphrase="pw")
    info = await inspect_private_key(private, "pw")
    keyring = pathlib.Path(tempfile.mkdtemp(prefix="sg-", dir="/tmp")) / "gnupg"
    monkeypatch.setattr(git_credentials, "SIGNING_HOME", str(keyring))

    work = _remote_and_work(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    driver = LocalGitDriver(work, tmp_path / "shim")
    driver.home = home
    # The identity must come from the injected config, not from the repository's own.
    await driver.exec_command("s1", "git config --unset user.name; git config --unset user.email")
    bundle = git_credentials.GitCredentialBundle(
        token="ghs_read",
        mode="app_readonly",
        author_name="octocat",
        author_email="octo@example.com",
        signing=MemberSigning(info.fingerprint, "octo@example.com", private, "pw"),
    )

    await inject_git_credentials_into_sandbox(driver, "s1", bundle)  # type: ignore[arg-type]
    made = await driver.exec_command("s1", "git commit -q --allow-empty -m signed")
    assert made.exit_code == 0, made.stderr
    shown = (await driver.exec_command("s1", "git log -1 --format='%G?|%GF|%ae'")).stdout.strip()
    status, fingerprint, author = shown.split("|")
    assert status in ("G", "U"), f"a good signature, got {status}"
    assert fingerprint == info.fingerprint and author == "octo@example.com"
    assert (keyring / "pass").read_text() == "pw" and (keyring.stat().st_mode & 0o777) == 0o700

    await git_credentials.apply_signing_to_sandbox(driver, "s1", None)  # type: ignore[arg-type]
    assert not keyring.exists(), "the key leaves the sandbox when the turn ends"
    shutil.rmtree(keyring.parent, ignore_errors=True)
    plain = await driver.exec_command("s1", "git commit -q --allow-empty -m plain")
    assert plain.exit_code == 0, plain.stderr
    assert (await driver.exec_command("s1", "git log -1 --format=%G?")).stdout.strip() == "N"
