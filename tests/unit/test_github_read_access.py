"""Tests for repo-aware _github_read_access callback in api/turns/callbacks.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from api.services.git_credentials import GitCredentialBundle
from api.turns.callbacks import _github_read_access


@pytest.fixture
def mock_ctx() -> SimpleNamespace:
    engine = AsyncMock()
    engine.settings = SimpleNamespace(
        github_api_url="https://api.github.com",
        bot_author_name="Bot",
        bot_author_email="bot@example.org",
    )
    req = SimpleNamespace(git_engine=engine, db_mgr=None)
    session = SimpleNamespace(tenant_org_id="test_org", tenant_user_id="user_1")
    return SimpleNamespace(req=req, session=session)


async def test_member_token_wins_when_present(mock_ctx: SimpleNamespace) -> None:
    bundle = GitCredentialBundle(
        token="gho_bundle_token",
        mode="member",
        author_name="User",
        author_email="user@example.com",
        user_token="gho_user_token",
        source_details={"username": "octocat"},
    )
    read_fn = _github_read_access(mock_ctx, bundle)  # type: ignore[arg-type]

    access = await read_fn("acme/backend-shared-library")
    assert access.token == "gho_user_token"
    assert access.actor == "@octocat"


async def test_dynamic_app_token_minted_for_target_repository(
    mock_ctx: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = GitCredentialBundle(
        token=None,
        mode="public",
        author_name="User",
        author_email="user@example.com",
        user_token=None,
        source_details={},
    )
    mock_ctx.req.git_engine.get_github_access_token.return_value = "ghs_app_dyn_token"

    # Mock installations_for_org
    inst = SimpleNamespace(installation_id=4242, account_login="acme")
    monkeypatch.setattr(
        "api.services.github_read_access.installations_for_org",
        AsyncMock(return_value=[inst]),
    )

    read_fn = _github_read_access(mock_ctx, bundle)  # type: ignore[arg-type]
    access = await read_fn("acme/backend-shared-library")

    assert access.token == "ghs_app_dyn_token"
    assert access.actor == "the organization's GitHub App"
    mock_ctx.req.git_engine.get_github_access_token.assert_awaited_once_with(
        repo="acme/backend-shared-library",
        repositories=["backend-shared-library"],
        installation_id=4242,
        inherit_permissions=True,
    )


async def test_falls_back_to_bundle_token_when_repo_not_resolved(
    mock_ctx: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = GitCredentialBundle(
        token="ghs_existing_bundle_token",
        mode="app",
        author_name="Bot",
        author_email="bot@example.org",
        user_token=None,
        source_details={"auth_type": "github_app"},
    )
    # No installations match
    monkeypatch.setattr(
        "api.services.github_read_access.installations_for_org",
        AsyncMock(return_value=[]),
    )

    read_fn = _github_read_access(mock_ctx, bundle)  # type: ignore[arg-type]
    access = await read_fn(None)

    assert access.token == "ghs_existing_bundle_token"
    assert access.actor == "the organization's GitHub App"
