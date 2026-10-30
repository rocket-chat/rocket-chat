"""What each commit signing mode does to a member's commits and to how the branch is published."""

from __future__ import annotations

import json
from typing import Any

import pytest
from agent_core.tools.git_tools import commit_spec_for_policy, publish_spec_for_policy
from api.services.credentials import store_user_credential
from api.services.git_signing import effective_signing_policy, member_signing_for_turn
from api.services.settings import effective_settings, save_org_config
from api.turns.shared import _turn_service

from specifications.interfaces.git import SigningMode

FINGERPRINT = "ABCD" * 10


async def _policy(org: str, mode: str) -> Any:
    await save_org_config(None, org, "github", {"signing": mode}, [])
    plan = await _turn_service.plan_turn(
        db_mgr=None, org_id=org, user_id="u1", agent_id=None, global_default_model="p/m"
    )
    return plan.policy.git


async def _member(org: str, *, key: bool) -> Any:
    if key:
        await store_user_credential(
            None,
            "u1",
            org,
            "git_signing",
            json.dumps({"private_key": "KEY", "passphrase": ""}),
            {"fingerprint": FINGERPRINT, "commit_email": "octo@example.com"},
        )
    prefs = await effective_settings(None, org, "user_preferences", "u1")
    return await member_signing_for_turn(None, org, "u1", prefs)


@pytest.mark.setting("github.signing")
async def test_github_mode_publishes_through_the_api_as_the_app() -> None:
    policy = effective_signing_policy(await _policy("sign_app", "github"), None)

    assert policy.signing == "github"
    assert publish_spec_for_policy(policy).signing_mode is SigningMode.GITHUB_APP
    assert publish_spec_for_policy(policy).token is None, "the App's own token"
    assert commit_spec_for_policy("m", None, None, policy).signing_mode is SigningMode.GITHUB_APP


@pytest.mark.setting("github.signing")
async def test_member_gpg_signs_with_the_members_key_when_they_stored_one() -> None:
    org = "sign_gpg_on"
    signing = await _member(org, key=True)
    base = await _policy(org, "member_gpg")
    policy = effective_signing_policy(
        type(base)(**{**base.__dict__, "user_name": "octocat", "user_token": "gho_m"}), signing
    )

    commit = commit_spec_for_policy("m", None, None, policy)
    assert commit.signing_mode is SigningMode.GPG_KEY and commit.gpg_key_id == FINGERPRINT
    assert (commit.committer_name, commit.committer_email) == ("octocat", "octo@example.com")
    publish = publish_spec_for_policy(policy)
    assert publish.signing_mode is SigningMode.GPG_KEY and publish.token == "gho_m"


@pytest.mark.setting("github.signing")
async def test_member_gpg_without_a_stored_key_falls_back_to_github_signed_commits() -> None:
    org = "sign_gpg_off"
    no_key = await _member(org, key=False)
    assert no_key is None
    base = await _policy(org, "member_gpg")

    policy = effective_signing_policy(base, no_key)

    assert policy.signing == "github" and not policy.commits_as_member
    assert publish_spec_for_policy(policy).signing_mode is SigningMode.GITHUB_APP


@pytest.mark.setting("github.signing")
async def test_none_mode_leaves_commits_unsigned_and_pushes_them_as_they_are() -> None:
    policy = effective_signing_policy(await _policy("sign_none", "none"), None)

    assert commit_spec_for_policy("m", None, None, policy).signing_mode is SigningMode.NONE
    assert publish_spec_for_policy(policy).signing_mode is SigningMode.NONE
