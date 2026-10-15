"""Commits leave the sandbox only through ``git_push``: what the sandbox is given, and the guard."""

from __future__ import annotations

import pathlib
import subprocess
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
        assert "prefer_app" not in kwargs, "there is no deployment-wide token to prefer"
        return "ghs_app_readonly"


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

    assert bundle.mode == "app" and bundle.token == "ghs_app_readonly"
    assert bundle.user_token == "gho_member", "kept for git_push and pull requests"
    assert bundle.author_name == "octocat", "commits are still made as the member"
    assert len(engine.requests) == 1, "one App token request, nothing else stands in for it"


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

    assert bundle.mode == "member" and bundle.token == "gho_member"


async def test_a_member_with_nothing_linked_still_reads_with_the_deployments_token() -> None:
    bundle = await resolve_session_git_credentials(
        "unlinked_user",
        ORG,
        _Engine(),
        None,
        {},  # type: ignore[arg-type]
    )

    assert bundle.mode == "app" and bundle.token == "ghs_app_readonly"
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
        mode="app",
        author_name="octocat",
        author_email="o@x.test",
    )

    await inject_git_credentials_into_sandbox(
        driver,  # type: ignore[arg-type]
        "s1",
        bundle,
        org_id="o",
        user_id="u",
        api_url="https://api.github.com",
    )

    refused = await driver.exec_command("s1", "git push origin HEAD:refs/heads/x")
    assert refused.exit_code != 0 and "git_push tool" in refused.stderr
    # No variable the agent can set lifts the refusal (publishing happens on the platform).
    still = await driver.exec_command(
        "s1", "git push origin HEAD:refs/heads/x", env={"ROCKET_PUBLISH": "1"}
    )
    assert still.exit_code != 0
    assert "ghs_app_readonly" not in str(driver.session_env), "the sandbox holds no token"


async def test_a_commit_made_in_the_sandbox_is_never_signed_and_no_key_enters_it(
    tmp_path: pathlib.Path,
) -> None:
    if not gpg_available():
        pytest.skip("needs GnuPG")
    private, _ = make_key(tmp_path, "Octo Cat <octo@example.com>", passphrase="pw")
    info = await inspect_private_key(private, "pw")
    work = _remote_and_work(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    driver = LocalGitDriver(work, tmp_path / "shim")
    driver.home = home
    bundle = git_credentials.GitCredentialBundle(
        token="ghs_read",
        mode="app",
        author_name="octocat",
        author_email="octo@example.com",
        signing=MemberSigning(info.fingerprint, "octo@example.com", private, "pw"),
    )

    await inject_git_credentials_into_sandbox(
        driver,  # type: ignore[arg-type]
        "s1",
        bundle,
        org_id="o",
        user_id="u",
        api_url="https://api.github.com",
    )
    made = await driver.exec_command("s1", "git commit -q --allow-empty -m plain")

    assert made.exit_code == 0, made.stderr
    assert (await driver.exec_command("s1", "git log -1 --format=%G?")).stdout.strip() == "N"
    every_command = " ".join(command for command, _ in driver.calls)
    assert "PRIVATE KEY" not in every_command and private not in every_command
    assert "PRIVATE KEY" not in str(driver.session_env)
    assert "gpgsign" not in (await driver.exec_command("s1", "git config --global -l")).stdout
