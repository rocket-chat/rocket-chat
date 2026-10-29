"""A member's own git policy: authorship only.

Authorship follows the organization until the member changes it, keeps following the
organization's later changes while left at its default, and stays the organization's when it
is locked. Pull request identity and signing are the organization's alone: a member choosing
``as_bot`` or unsigned commits would publish past the organization's policy.
"""

from __future__ import annotations

from typing import Any

import pytest
from api.main import app
from api.services.settings import save_org_config, save_user_config
from api.turns.shared import _turn_service
from fastapi.testclient import TestClient

ORG = "git_member_policy"


async def _git_policy(org: str, user: str) -> Any:
    policy = await _turn_service._resolve_policy(None, org, user, None)
    return policy.git


@pytest.mark.setting("github.commit_signing_mode")
@pytest.mark.setting("github.commit_authorship_policy")
@pytest.mark.setting("github.pr_creation_policy")
async def test_only_authorship_is_the_members_choice_and_a_lock_still_wins() -> None:
    org = f"{ORG}_override"
    await save_org_config(
        None,
        org,
        "github",
        {"commit_signing_mode": "github_app", "commit_authorship_policy": "co_authored"},
        [],
    )
    # Stored before these keys became organization-only: they must not resurface.
    await save_user_config(
        None,
        org,
        "nathan",
        "github",
        {
            "commit_signing_mode": "none",
            "pr_creation_policy": "as_bot",
            "commit_authorship_policy": "user_only",
        },
    )

    mine = await _git_policy(org, "nathan")
    theirs = await _git_policy(org, "alice")

    assert (mine.signing_mode, mine.pr_creation) == ("github_app", "as_user")
    assert mine.authorship == "user_only"
    assert theirs.authorship == "co_authored"

    await save_org_config(
        None,
        org,
        "github",
        {"commit_authorship_policy": "bot_only"},
        ["commit_authorship_policy"],
    )
    assert (await _git_policy(org, "nathan")).authorship == "bot_only", "a lock wins"


def test_a_member_cannot_choose_the_pull_request_identity_or_signing() -> None:
    headers = {"X-Tenant-Org-Id": f"{ORG}_refused", "X-Tenant-User-Id": "nathan"}
    with TestClient(app) as client:
        for key, value in (("pr_creation_policy", "as_bot"), ("commit_signing_mode", "none")):
            response = client.put(
                "/v1/settings/github", json={"overrides": {key: value}}, headers=headers
            )
            assert response.status_code in (403, 422), response.text


async def test_a_reset_field_follows_the_organizations_later_changes() -> None:
    org = f"{ORG}_reset"
    headers = {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": "nathan"}
    with TestClient(app) as client:
        saved = client.put(
            "/v1/settings/github",
            json={"overrides": {"commit_authorship_policy": "user_only"}},
            headers=headers,
        ).json()
        assert saved["field_status"]["commit_authorship_policy"] == "overridden"

        reset = client.delete(
            "/v1/settings/github/commit_authorship_policy", headers=headers
        ).json()
        assert reset["field_status"]["commit_authorship_policy"] == "org_default"

        # Through the API, so the same store is written whether or not a database is in use.
        admin = {**headers, "X-Tenant-User-Id": "admin", "X-User-Role": "admin"}
        client.put(
            "/v1/admin/settings/github",
            json={"config": {"commit_authorship_policy": "bot_only"}, "locked_keys": []},
            headers=admin,
        ).raise_for_status()
        later = client.get("/v1/settings/github", headers=headers).json()

    assert later["effective"]["commit_authorship_policy"] == "bot_only", (
        "left at default, it follows"
    )


def test_the_bot_identity_and_app_permissions_stay_the_organizations() -> None:
    headers = {"X-Tenant-Org-Id": f"{ORG}_bot", "X-Tenant-User-Id": "nathan"}
    with TestClient(app) as client:
        for key, value in (
            ("bot_author_name", "Me"),
            ("app_token_permissions", {"contents": "write"}),
            ("app_token_target_repositories", "all"),
        ):
            res = client.put(
                "/v1/settings/github", json={"overrides": {key: value}}, headers=headers
            )
            assert res.status_code == 422, key
