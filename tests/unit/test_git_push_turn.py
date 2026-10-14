"""A whole turn: ``git_push`` publishes the way the organization and the member's choice say."""

from __future__ import annotations

import json
from typing import Any

import pytest
from api.services.credentials import store_user_credential
from api.services.settings import save_org_config, save_user_config
from turn_harness import run_turn

from specifications.interfaces.git import PublishResult, PublishSpec, SigningMode

FINGERPRINT = "ABCD" * 10


class _Engine:
    def __init__(self) -> None:
        self.specs: list[PublishSpec] = []

    async def publish_branch(self, session_id: str, spec: PublishSpec) -> PublishResult:
        self.specs.append(spec)
        return PublishResult(
            "acme/widgets",
            "feat/x",
            "f" * 40,
            via="github_api" if spec.signing_mode is SigningMode.GITHUB_APP else "git_push",
            commits=2,
            notices=["tool.sh was published without its executable bit."],
        )


async def _push(org: str, engine: _Engine) -> str:
    outcome = await run_turn(
        org, script=[("git_push", {})], user="u1", git_engine=engine, final_text="Published."
    )
    done = outcome.events_of("tool_completed")
    assert done and done[0].payload["name"] == "git_push"
    return str(done[0].payload["result"])


@pytest.mark.setting("github.commit_signing_mode")
async def test_a_turn_publishes_through_the_api_so_github_signs_the_commits() -> None:
    org = "push_turn_app"
    await save_org_config(None, org, "github", {"commit_signing_mode": "github_app"}, [])
    engine = _Engine()

    result = await _push(org, engine)

    assert engine.specs[0].signing_mode is SigningMode.GITHUB_APP
    assert (
        "Published 2 commit(s) to acme/widgets@feat/x" in result and "GitHub signed them" in result
    )
    assert "executable bit" in result, "what the member should know is passed on"


@pytest.mark.setting("github.commit_signing_mode")
@pytest.mark.setting("user_preferences.commit_as_me")
async def test_a_turn_signs_with_the_members_key_when_they_chose_to_commit_as_themselves() -> None:
    org = "push_turn_gpg"
    await save_org_config(None, org, "github", {"commit_signing_mode": "gpg_key"}, [])
    await store_user_credential(
        None,
        "u1",
        org,
        "git_signing",
        json.dumps({"private_key": "KEY", "passphrase": ""}),
        {"fingerprint": FINGERPRINT, "commit_email": "octo@example.com"},
    )
    await save_user_config(None, org, "u1", "user_preferences", {"commit_as_me": True})
    engine = _Engine()

    result = await _push(org, engine)

    assert engine.specs[0].signing_mode is SigningMode.GPG_KEY
    assert result.startswith("Pushed acme/widgets@feat/x")


@pytest.mark.setting("github.commit_signing_mode")
async def test_gpg_mode_without_a_key_publishes_github_signed_commits_instead() -> None:
    org = "push_turn_no_key"
    await save_org_config(None, org, "github", {"commit_signing_mode": "gpg_key"}, [])
    engine = _Engine()

    await _push(org, engine)

    assert engine.specs[0].signing_mode is SigningMode.GITHUB_APP


async def test_a_turn_without_a_github_engine_says_so_instead_of_pretending() -> None:
    outcome: Any = await run_turn("push_turn_none", script=[("git_push", {})], final_text="Done.")

    result = outcome.events_of("tool_completed")[0].payload["result"]
    assert result.startswith("Error:") and "GitHub is not configured" in result
