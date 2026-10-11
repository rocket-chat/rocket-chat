"""Blank sessions, repository validation and organization-scoped GitHub App installations."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from agent_core.tools.repository import clone_into_workspace
from api.main import app
from api.services import github_installations as installations
from api.services.git_credentials import resolve_session_git_credentials
from api.services.repository_hint import with_repository_hint
from api.services.repository_validation import (
    InvalidRepositoryError,
    parse_repository,
    validate_branch,
)
from fastapi.testclient import TestClient
from git_engine.config import GitEngineSettings
from git_engine.engine import GitEngine, pick_installation
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult

# --- repository validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, slug",
    [
        ("acme/widgets", "acme/widgets"),
        ("https://github.com/acme/widgets", "acme/widgets"),
        ("https://github.com/acme/widgets.git", "acme/widgets"),
        ("  https://GitHub.com/acme/widgets/ ", "acme/widgets"),
    ],
)
def test_accepted_repository_forms_normalise_to_one_https_url(raw: str, slug: str) -> None:
    ref = parse_repository(raw)
    assert (ref.slug, ref.url) == (slug, f"https://github.com/{slug}.git")


@pytest.mark.parametrize(
    "raw",
    [
        "file:///etc",
        "/etc/passwd",
        "git@github.com:acme/widgets.git",
        "http://github.com/acme/widgets",
        "https://evil.example/acme/widgets",
        "https://user:pw@github.com/acme/widgets",
        "https://github.com/acme/widgets?x=1",
        "acme/widgets; rm -rf /",
        "../../etc",
        "",
    ],
)
def test_anything_that_could_read_local_files_or_leak_credentials_is_refused(raw: str) -> None:
    with pytest.raises(InvalidRepositoryError):
        parse_repository(raw)


def test_a_self_hosted_github_host_can_be_configured() -> None:
    assert parse_repository("https://git.corp.example/a/b", host="git.corp.example").slug == "a/b"


@pytest.mark.parametrize("branch", ["main", "release/1.2", "feat/x_y-z"])
def test_ordinary_branch_names_pass(branch: str) -> None:
    assert validate_branch(branch) == branch


@pytest.mark.parametrize("branch", ["--upload-pack=x", "a..b", "x;y", "bad.lock", "-d", ""])
def test_branch_names_that_could_act_as_options_or_escape_are_refused(branch: str) -> None:
    with pytest.raises(InvalidRepositoryError):
        validate_branch(branch)


# --- the agent clones; the platform does not --------------------------------------------


class _RecordingDriver(FakeSandboxDriver):
    def __init__(self, exit_code: int = 0) -> None:
        super().__init__()
        self.commands: list[str] = []
        self.exit_code = exit_code

    async def exec_command(self, session_id: str, command: str, *a: Any, **k: Any) -> ExecResult:
        self.commands.append(command)
        return ExecResult(self.exit_code, "", "", 1)


async def test_git_is_installed_first_when_the_image_lacks_it() -> None:
    driver = _RecordingDriver()
    await clone_into_workspace(driver, "s", "a/b")

    assert driver.commands[0].startswith("command -v git")
    assert "apt-get install" in driver.commands[0] and "apk add" in driver.commands[0]


async def test_a_sandbox_that_cannot_get_git_says_so_instead_of_trying_to_clone() -> None:
    driver = _RecordingDriver(exit_code=1)

    outcome = await clone_into_workspace(driver, "s", "a/b")

    assert outcome.folder is None and "git is not installed" in outcome.message
    assert len(driver.commands) == 1, "no clone is attempted without git"


def test_a_named_repository_becomes_a_line_of_the_instruction() -> None:
    prompt = with_repository_hint("Fix the bug", "acme/widgets", "develop")
    assert prompt.startswith("Repository for this task: `acme/widgets` (branch `develop`).")
    assert "clone_repository" in prompt and prompt.endswith("Fix the bug")
    assert with_repository_hint("Fix the bug", None) == "Fix the bug"


# --- API ----------------------------------------------------------------------------------

HEADERS = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "repo_user",
    "X-User-Role": "developer",
}


def test_a_session_always_starts_blank(client: TestClient) -> None:
    created = client.post(
        "/v1/sessions",
        json={"title": "repo", "git_repo": "acme/widgets", "git_branch": "develop"},
        headers=HEADERS,
    )
    assert created.status_code == 200
    body = created.json()
    assert (body["git_repo"], body["git_branch"]) == (None, None)


# --- installations ------------------------------------------------------------------------


def test_the_right_installation_is_picked_without_guessing() -> None:
    mapped = [(1, "acme"), (2, "Globex")]
    assert pick_installation(mapped, "globex/app") == 2
    assert pick_installation(mapped, "unknown/app") is None  # two candidates: refuse to guess
    assert pick_installation([(7, "solo")], "other/app") == 7
    assert pick_installation([], "acme/app") is None


async def test_an_unmapped_organization_gets_no_app_token_by_default() -> None:
    engine = GitEngine(settings=GitEngineSettings(github_app_id="1", github_app_private_key="k"))
    assert await engine.get_github_access_token(repo="acme/widgets") is None


async def test_credentials_use_only_the_organizations_own_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(installations, "_memory", {})
    await installations.bind_installation(None, 11, "org_a", "acme", "admin")
    await installations.bind_installation(None, 22, "org_b", "globex", "admin")
    engine = GitEngine(settings=GitEngineSettings(github_app_id="1", github_app_private_key="k"))
    engine.get_github_access_token = AsyncMock(return_value="tok")  # type: ignore[method-assign]

    await resolve_session_git_credentials("u", "org_a", engine, None, repo="globex/secret")
    assert engine.get_github_access_token.call_args.kwargs["installation_id"] == 11

    await resolve_session_git_credentials("u", "org_c", engine, None, repo="acme/widgets")
    assert engine.get_github_access_token.call_args.kwargs["installation_id"] is None


async def test_an_installation_belongs_to_one_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installations, "_memory", {})
    await installations.bind_installation(None, 5, "org_a", "acme", "admin")
    with pytest.raises(ValueError, match="another organization"):
        await installations.bind_installation(None, 5, "org_b", "acme", "admin")
    assert await installations.unbind_installation(None, 5, "org_b") is False
    assert await installations.unbind_installation(None, 5, "org_a") is True


ADMIN = {"X-Tenant-Org-Id": "gh_org", "X-Tenant-User-Id": "gh_admin", "X-User-Role": "admin"}


def test_the_install_flow_maps_an_installation_only_with_a_matching_signed_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(installations, "_memory", {})
    engine = GitEngine(
        settings=GitEngineSettings(
            github_app_id="1", github_app_private_key="k", github_app_slug="rocket-app"
        )
    )
    engine.get_installation_account = AsyncMock(return_value="acme")  # type: ignore[method-assign]
    app.state.git_engine = engine

    listing = client.get("/v1/admin/github/installations", headers=ADMIN).json()
    assert listing["app_configured"] is True
    url = listing["install_url"]
    assert url.startswith("https://github.com/apps/rocket-app/installations/new?state=")
    state = url.split("state=", 1)[1]

    # A forged state, another administrator's state and a non-admin are all refused.
    assert (
        client.get("/v1/github/setup?installation_id=9&state=forged", headers=ADMIN).status_code
        == 400
    )
    other = {**ADMIN, "X-Tenant-User-Id": "someone_else"}
    assert (
        client.get(f"/v1/github/setup?installation_id=9&state={state}", headers=other).status_code
        == 403
    )
    member = {**ADMIN, "X-User-Role": "developer"}
    assert (
        client.get(f"/v1/github/setup?installation_id=9&state={state}", headers=member).status_code
        == 403
    )

    done = client.get(
        f"/v1/github/setup?installation_id=9&state={state}", headers=ADMIN, follow_redirects=False
    )
    assert done.status_code == 303 and done.headers["location"].startswith("/settings/github")
    mapped = client.get("/v1/admin/github/installations", headers=ADMIN).json()["installations"]
    assert mapped == [{"installation_id": 9, "account_login": "acme"}]

    assert client.delete("/v1/admin/github/installations/9", headers=ADMIN).status_code == 200
    assert client.delete("/v1/admin/github/installations/9", headers=ADMIN).status_code == 404


def test_an_installation_github_does_not_know_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(installations, "_memory", {})
    engine = GitEngine(
        settings=GitEngineSettings(
            github_app_id="1", github_app_private_key="k", github_app_slug="s"
        )
    )
    engine.get_installation_account = AsyncMock(return_value=None)  # type: ignore[method-assign]
    app.state.git_engine = engine
    state = (
        client.get("/v1/admin/github/installations", headers=ADMIN)
        .json()["install_url"]
        .split("state=", 1)[1]
    )
    assert (
        client.get(f"/v1/github/setup?installation_id=404&state={state}", headers=ADMIN).status_code
        == 400
    )


def test_webhook_events_are_attributed_to_the_installations_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.routers.webhooks import _webhook_identity

    class _Req:
        class app:
            class state:
                db_manager = None

    monkeypatch.setattr(installations, "_memory", {})
    asyncio.run(installations.bind_installation(None, 31, "org_mapped", "acme", "a"))
    monkeypatch.setenv("ROCKET_WEBHOOK_ORG_ID", "fallback_org")
    assert asyncio.run(_webhook_identity(_Req(), "bob", 31))[0] == "org_mapped"  # type: ignore[arg-type]
    assert asyncio.run(_webhook_identity(_Req(), "bob", 999))[0] == "fallback_org"  # type: ignore[arg-type]
    assert asyncio.run(_webhook_identity(_Req(), "bob", None))[0] == "fallback_org"  # type: ignore[arg-type]
