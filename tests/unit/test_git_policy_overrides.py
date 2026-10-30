"""A member's own git policy: who publishes, nothing else.

``github.publish_as`` follows the organization until the member changes it, keeps following
the organization's later changes while left at its default, and stays the organization's when
it is locked; choosing ``bot`` needs the ``publish_as_bot`` capability. Signing is the
organization's alone: a member choosing unsigned commits would publish past its policy.
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


@pytest.mark.setting("github.publish_as")
async def test_publish_as_is_the_members_choice_and_a_lock_still_wins() -> None:
    org = f"{ORG}_override"
    await save_org_config(None, org, "github", {"publish_as": "bot", "signing": "github"}, [])
    # ``signing`` stored as a member value before it became organization-only: it must not
    # resurface.
    await save_user_config(
        None, org, "nathan", "github", {"publish_as": "member", "signing": "none"}
    )

    mine = await _git_policy(org, "nathan")
    theirs = await _git_policy(org, "alice")

    assert (mine.publish_as, mine.signing) == ("member", "github")
    assert theirs.publish_as == "bot", "the organization's default"

    await save_org_config(None, org, "github", {"publish_as": "bot"}, ["publish_as"])
    assert (await _git_policy(org, "nathan")).publish_as == "bot", "a lock wins"


@pytest.mark.setting("github.publish_as")
@pytest.mark.setting("access.publish_as_bot")
async def test_choosing_bot_needs_the_publish_as_bot_capability() -> None:
    org = f"{ORG}_capability"
    await save_user_config(None, org, "nathan", "github", {"publish_as": "bot"})

    refused = await _git_policy(org, "nathan")
    assert refused.publish_as == "member", "members lack publish_as_bot by default"

    await save_org_config(None, org, "access", {"publish_as_bot": ["owner", "admin", "member"]}, [])
    assert (await _git_policy(org, "nathan")).publish_as == "bot"


@pytest.mark.setting("github.signing")
def test_a_member_cannot_choose_signing() -> None:
    headers = {"X-Tenant-Org-Id": f"{ORG}_refused", "X-Tenant-User-Id": "nathan"}
    with TestClient(app) as client:
        response = client.put(
            "/v1/settings/github", json={"overrides": {"signing": "none"}}, headers=headers
        )
        assert response.status_code in (403, 422), response.text


async def test_a_reset_field_follows_the_organizations_later_changes() -> None:
    org = f"{ORG}_reset"
    headers = {"X-Tenant-Org-Id": org, "X-Tenant-User-Id": "nathan"}
    with TestClient(app) as client:
        saved = client.put(
            "/v1/settings/github",
            json={"overrides": {"publish_as": "member"}},
            headers=headers,
        ).json()
        assert saved["provenance"]["publish_as"]["source"] == "member"

        reset = client.delete("/v1/settings/github/publish_as", headers=headers).json()
        assert reset["provenance"]["publish_as"]["source"] == "platform"

        # Through the API, so the same store is written whether or not a database is in use.
        admin = {**headers, "X-Tenant-User-Id": "admin", "X-User-Role": "admin"}
        client.put(
            "/v1/admin/settings/github",
            json={"config": {"publish_as": "bot"}, "locked_keys": []},
            headers=admin,
        ).raise_for_status()
        later = client.get("/v1/settings/github", headers=headers).json()

    assert later["effective"]["publish_as"] == "bot", "left at default, it follows"
    assert later["provenance"]["publish_as"]["source"] == "org"


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
