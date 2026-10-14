"""Committing as the member: GitHub username and e-mail by default, the git panel overrides."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from api.services import git_credentials, github_identity
from api.services.credentials import store_user_credential
from api.services.git_credentials import resolve_session_git_credentials
from api.services.github_identity import commit_identity, fetch_github_identity

LINKED = {
    "login": "octocat",
    "github_id": 583231,
    "email": "octo@example.com",
    "noreply_email": "583231+octocat@users.noreply.github.com",
}


def test_the_github_account_is_the_default_identity() -> None:
    identity = commit_identity({"full_name": "Octavia Cat"}, LINKED, "u1")
    assert (identity.name, identity.name_source) == ("octocat", "github")
    assert (identity.email, identity.email_source) == ("octo@example.com", "github")


def test_the_noreply_address_stands_in_when_github_has_no_public_email() -> None:
    identity = commit_identity({}, {**LINKED, "email": ""}, "u1")
    assert identity.email == "583231+octocat@users.noreply.github.com"
    assert identity.email_source == "github_noreply"


def test_the_git_panel_overrides_each_part_on_its_own() -> None:
    identity = commit_identity({"git_author_email": "me@work.test"}, LINKED, "u1")
    assert (identity.name, identity.name_source) == ("octocat", "github")
    assert (identity.email, identity.email_source) == ("me@work.test", "git_panel")
    named = commit_identity({"git_author_name": "O. Cat"}, LINKED, "u1")
    assert (named.name, named.name_source) == ("O. Cat", "git_panel")


def test_without_a_github_account_the_profile_name_and_a_placeholder_are_used() -> None:
    identity = commit_identity({"full_name": "Octavia Cat"}, None, "u1")
    assert (identity.name, identity.name_source) == ("Octavia Cat", "profile")
    assert identity.email == "u1@rocket-chat.internal" and identity.email_source == "default"
    assert commit_identity({}, None, "u1").name == "Rocket Developer"


def _github(routes: dict[str, tuple[int, Any]], monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        status, body = routes.get(request.url.path, (404, {}))
        return httpx.Response(status, json=body)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        github_identity.httpx,
        "AsyncClient",
        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs),
    )
    return seen


async def test_the_primary_verified_email_is_preferred_over_the_public_profile_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _github(
        {
            "/user": (
                200,
                {"login": "octocat", "id": 583231, "name": "Octo", "email": "pub@x.test"},
            ),
            "/user/emails": (
                200,
                [
                    {"email": "other@x.test", "primary": False, "verified": True},
                    {"email": "Primary@X.test", "primary": True, "verified": True},
                ],
            ),
        },
        monkeypatch,
    )
    found = await fetch_github_identity("token")
    assert found["email"] == "primary@x.test"
    assert found["noreply_email"] == "583231+octocat@users.noreply.github.com"


async def test_a_token_that_cannot_read_emails_falls_back_to_the_public_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _github({"/user": (200, {"login": "octocat", "id": 1, "email": "pub@x.test"})}, monkeypatch)
    assert (await fetch_github_identity("token"))["email"] == "pub@x.test"


async def test_an_unreachable_github_yields_no_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    _github({"/user": (401, {})}, monkeypatch)
    assert await fetch_github_identity("token") == {}


async def test_a_turn_commits_as_the_linked_account_and_remembers_what_it_learned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = "identity_user"
    await store_user_credential(None, user, "org-1", "github", "gho_token", {"login": "octocat"})
    seen = _github(
        {
            "/user": (200, {"login": "octocat", "id": 583231}),
            "/user/emails": (
                200,
                [{"email": "octo@example.com", "primary": True, "verified": True}],
            ),
        },
        monkeypatch,
    )

    async def valid(*_: Any, **__: Any) -> bool:
        return True

    monkeypatch.setattr(git_credentials, "github_token_is_valid", valid)
    monkeypatch.setattr(git_credentials, "refresh_github_user_token", _unchanged)

    first = await resolve_session_git_credentials(
        user, "org-1", None, None, {"full_name": "Octavia"}
    )
    assert (first.author_name, first.author_email) == ("octocat", "octo@example.com")
    assert first.identity is not None and first.identity.name_source == "github"

    lookups = len(seen)
    again = await resolve_session_git_credentials(user, "org-1", None, None, {})
    assert again.author_email == "octo@example.com" and len(seen) == lookups, "asked GitHub once"

    panel = await resolve_session_git_credentials(
        user, "org-1", None, None, {"git_author_name": "O. Cat", "git_author_email": "me@work.test"}
    )
    assert (panel.author_name, panel.author_email) == ("O. Cat", "me@work.test")


async def _unchanged(_db: Any, credential: Any) -> Any:
    return credential
