"""No GitHub token, signing key or passphrase reaches the sandbox or the model, in any mode.

A whole turn clones, runs a shell, publishes and comments on a pull request, for every
credential source (the App, the member's account, none) and every signing mode. Every command
the sandbox ran, its environment, the files written into it, the published events and every
model request are searched for the secrets the platform holds.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from agent_core.tools import github_client
from api.services import git_credentials
from api.services.credentials import store_user_credential
from api.services.settings import save_org_config
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.git import PublishResult, PublishSpec
from specifications.interfaces.sandbox import ExecResult
from tests.turn_harness import run_turn
from tests.unit.test_github_work_tools import FakeGitHub

MEMBER_TOKEN = "gho_member_secret_" + "m" * 20
APP_TOKEN = "ghs_app_secret_" + "a" * 24
PRIVATE_KEY = "-----BEGIN PGP PRIVATE KEY BLOCK-----\nsecret-key-material\n"
PASSPHRASE = "correct-horse-battery-staple"
SECRETS = (MEMBER_TOKEN, APP_TOKEN, "secret-key-material", PASSPHRASE)
COMMENT = {"repository": "acme/app", "action": "comment", "number": 7, "body": "Fixed."}
SCRIPT = [
    ("clone_repository", {"repository": "acme/app"}),
    ("bash_exec", {"command": "env; cat ~/.gitconfig; ls /dev/shm"}),
    ("git_push", {}),
    ("github_pull_request", COMMENT),
]


class _Engine:
    settings = SimpleNamespace(
        github_api_url="https://api.github.com", bot_author_name="Bot", bot_author_email="b@x"
    )

    def __init__(self, app: bool) -> None:
        self.app = app
        self.published: list[PublishSpec] = []

    async def get_github_access_token(self, **kwargs: Any) -> str | None:
        return APP_TOKEN if self.app else None

    async def publish_branch(self, session_id: str, spec: PublishSpec) -> PublishResult:
        self.published.append(spec)
        return PublishResult("acme/app", "fix/x", "c" * 40, "git_push", commits=1)


@pytest.fixture(autouse=True)
def github(monkeypatch: pytest.MonkeyPatch) -> None:
    async def yes(*_: Any, **__: Any) -> bool:
        return True

    async def same(_db: Any, credential: Any, *_: Any) -> Any:
        return credential

    async def none(*_: Any, **__: Any) -> list[Any]:
        return []

    monkeypatch.setattr(git_credentials, "github_token_is_valid", yes)
    monkeypatch.setattr(git_credentials, "_token_reads_repo", yes)
    monkeypatch.setattr(git_credentials, "refresh_github_user_token", same)
    monkeypatch.setattr(git_credentials, "with_github_identity", same)
    monkeypatch.setattr(git_credentials, "installations_for_org", none)
    fake, real = FakeGitHub(), httpx.AsyncClient
    monkeypatch.setattr(
        github_client.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(fake.handler), **kw),
    )


async def _configure(org: str, user: str, member: bool, signing: str) -> None:
    await save_org_config(None, org, "github", {"signing": signing}, [])
    if not member:
        return
    await store_user_credential(None, user, org, "github", MEMBER_TOKEN, {"login": "octocat"})
    await store_user_credential(
        None, user, org, "git_signing",
        json.dumps({"private_key": PRIVATE_KEY, "passphrase": PASSPHRASE}),
        {"fingerprint": "ABCD" * 10, "commit_email": "octo@example.com"},
    )  # fmt: skip


@pytest.mark.parametrize("signing", ["github", "member_gpg", "none"])
@pytest.mark.parametrize("source", ["app", "member", "app+member", "none"])
async def test_no_secret_reaches_the_sandbox_or_the_model(source: str, signing: str) -> None:
    org = f"org_nosecret_{source.replace('+', '_')}_{signing}"
    # A user of its own: the in-memory credential store is shared by the whole test run.
    user = f"user_{org}"
    await _configure(org, user, "member" in source, signing)
    driver = FakeSandboxDriver()
    driver.script_command("git clone", ExecResult(0, "main\nabc Initial\n", "", 1))
    engine = _Engine(app="app" in source)

    outcome = await run_turn(
        org,
        script=SCRIPT,
        driver=driver,
        git_engine=engine,
        user=user,
    )

    assert engine.published, "the turn did publish"
    if "member" in source and signing == "member_gpg":
        key = engine.published[0].signing_key
        assert key is not None and "secret-key-material" in key.private_key, "kept host-side"
    seen_by_sandbox = json.dumps(
        [driver.exec_calls, driver.exec_envs, driver.file_snapshot(f"session_{org}")],
        default=str,
    )
    seen_by_model = json.dumps([r.messages for r in outcome.model.requests], default=str)
    for secret in SECRETS:
        assert secret not in seen_by_sandbox, f"{secret[:12]}... reached the sandbox"
        assert secret not in seen_by_model, f"{secret[:12]}... reached the model"
        assert secret not in outcome.text_of_events(), f"{secret[:12]}... reached an event"
