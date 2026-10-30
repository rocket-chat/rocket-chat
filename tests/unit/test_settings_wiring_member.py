"""Member preferences change the model, the git identity, the prompt and the follow-ups."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from api.services.git_credentials import resolve_session_git_credentials
from api.services.settings import effective_settings, save_org_config, save_user_config
from api.turns.prompt import _organization_guidance
from api.turns.shared import _turn_service
from turn_harness import run_turn


async def _plan(org: str, **kwargs: Any) -> Any:
    return await _turn_service.plan_turn(
        db_mgr=None, org_id=org, user_id="u1", agent_id=None, global_default_model="p/m", **kwargs
    )


async def _prefs(org: str, **values: Any) -> None:
    await save_user_config(None, org, "u1", "user_preferences", values)


@pytest.mark.setting("user_preferences.preferred_model")
async def test_a_members_preferred_model_beats_the_organization_default() -> None:
    org = "wire_pref_model"
    await save_org_config(None, org, "models", {"default_model": "org/default"}, [])
    assert (await _plan(org)).model == "org/default"

    await _prefs(org, preferred_model="member/choice")
    plan = await _plan(org)

    assert plan.model == "member/choice"
    assert "saved model preference" in plan.routing_reason


@pytest.mark.setting("user_preferences.full_name")
@pytest.mark.setting("user_preferences.git_author_name")
@pytest.mark.setting("user_preferences.git_author_email")
async def test_the_members_identity_becomes_the_git_author() -> None:
    org = "wire_identity"
    await _prefs(org, full_name="Alice Doe")
    prefs = await effective_settings(None, org, "user_preferences", "u1")
    named = await resolve_session_git_credentials("u1", org, None, None, prefs)
    assert (named.author_name, named.author_email) == ("Alice Doe", "u1@rocket-chat.internal")

    await _prefs(org, full_name="Alice Doe", git_author_name="Ali", git_author_email="ali@x.io")
    explicit = (await _plan(org)).policy.git
    assert (explicit.user_name, explicit.user_email) == ("Ali", "ali@x.io")


@pytest.mark.setting("user_preferences.git_branch_prefix")
async def test_the_branch_prefix_is_told_to_the_agent() -> None:
    org = "wire_branch_prefix"
    ctx = SimpleNamespace(
        session=SimpleNamespace(tenant_org_id=org, git_repo=None, git_branch=None),
        req=SimpleNamespace(db_mgr=None),
        binding=SimpleNamespace(policy=SimpleNamespace(git=SimpleNamespace(default_branch="main"))),
    )

    without = await _organization_guidance(ctx, {})
    with_prefix = await _organization_guidance(ctx, {"git_branch_prefix": "ada/"})

    assert not any("Branch naming" in line for line in without)
    assert any("`ada/`" in line for line in with_prefix)


@pytest.mark.setting("user_preferences.permission_mode")
async def test_a_members_permission_mode_applies_when_the_session_sets_none() -> None:
    org = "wire_permission_mode"
    assert (await _plan(org)).policy.permission_mode == "auto"

    await _prefs(org, permission_mode="ask")

    assert (await _plan(org)).policy.permission_mode == "ask"
    assert (await _plan(org, session_permission_mode="plan")).policy.permission_mode == "plan"


@pytest.mark.setting("user_preferences.suggest_next_questions")
async def test_the_next_step_suggestion_is_offered_only_when_the_member_wants_it() -> None:
    answer = "Done. Next steps: Run the full test suite and review the changes carefully"

    async def suggestions(org: str) -> int:
        outcome = await run_turn(org, prompt="Fix the bug", user="u1", final_text=answer)
        return len(outcome.events_of("followup_suggestion"))

    assert await suggestions("wire_suggest_on") >= 1
    await _prefs("wire_suggest_off", suggest_next_questions=False)
    assert await suggestions("wire_suggest_off") == 0


@pytest.mark.setting("user_preferences.turn_notifications")
@pytest.mark.setting("user_preferences.notification_sound")
def test_turn_alerts_are_a_members_own_choice_the_cockpit_reads() -> None:
    """The cockpit reads these from the member's effective preferences (``turn-attention.ts``);
    the browser behaviour itself is covered by ``e2e/turn_attention.spec.ts``."""
    from api.main import app
    from fastapi.testclient import TestClient

    member = {"X-Tenant-Org-Id": "wire_alerts", "X-Tenant-User-Id": "u1"}
    other = {**member, "X-Tenant-User-Id": "u2"}
    with TestClient(app) as client:
        assert (
            client.get("/v1/settings/user_preferences", headers=member).json()["effective"][
                "turn_notifications"
            ]
            is False
        )
        client.put(
            "/v1/settings/user_preferences",
            json={"overrides": {"turn_notifications": True, "notification_sound": True}},
            headers=member,
        ).raise_for_status()
        mine = client.get("/v1/settings/user_preferences", headers=member).json()["effective"]
        theirs = client.get("/v1/settings/user_preferences", headers=other).json()["effective"]

    assert (mine["turn_notifications"], mine["notification_sound"]) == (True, True)
    assert (theirs["turn_notifications"], theirs["notification_sound"]) == (False, False)
