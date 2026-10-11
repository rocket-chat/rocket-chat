"""Slack settings change what the assistant does, end to end through the real turn pipeline."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from slack_assistant.policy import SlackOrgPolicy
from test_slack_live_experience import SESSION_ID, _live
from test_slack_shared_pipeline import THREAD, ScriptedModel, Stack


def _policy(**settings: Any) -> SlackOrgPolicy:
    return SlackOrgPolicy.from_settings(settings)


async def test_the_slack_toolset_is_offered_to_slack_turns() -> None:
    """Regression: the allowlist was computed from a view that hides toolset-scoped tools."""
    model = ScriptedModel()
    await Stack(model).send()

    offered = {tool.name for tool in model.agent_requests()[0].tools}
    assert {"slack_read_thread", "slack_send_message", "slack_send_dm"} <= offered


def _system_text(model: ScriptedModel) -> str:
    request = model.agent_requests()[0]
    return "\n".join(m.content or "" for m in request.messages if m.role == "system")


@pytest.mark.setting("slack.default_agent_persona")
async def test_the_default_persona_is_the_agent_used_in_threads() -> None:
    model = ScriptedModel()
    await Stack(model, _policy(default_agent_persona="persona-security")).send()

    assert "Security Auditor persona" in _system_text(model)


@pytest.mark.setting("slack.allowed_tools")
async def test_only_the_allowed_tools_are_offered_to_the_agent_in_slack() -> None:
    model = ScriptedModel()
    await Stack(model, _policy(allowed_tools=["file_read", "web_fetch"])).send()

    assert {tool.name for tool in model.agent_requests()[0].tools} == {"file_read", "web_fetch"}


@pytest.mark.setting("slack.channel_profiles")
async def test_a_channel_profile_overrides_persona_rules_and_tools_for_that_channel() -> None:
    model = ScriptedModel()
    profiles = {
        THREAD[0]: {
            "agent_persona": "persona-security",
            "instructions": "Answer in French.",
            "allowed_tools": ["file_read"],
        }
    }
    await Stack(model, _policy(channel_profiles=profiles)).send()

    assert "Answer in French." in _system_text(model)
    assert {tool.name for tool in model.agent_requests()[0].tools} == {"file_read"}


@pytest.mark.setting("slack.allowed_channel_types")
async def test_messages_in_a_channel_type_the_organization_excludes_start_no_work() -> None:
    model = ScriptedModel()
    stack = Stack(model, _policy(allowed_channel_types=["im"]))

    await stack.send()

    assert model.requests == []
    assert await stack.store.get_session(SESSION_ID) is None


@pytest.mark.setting("slack.dm_policy")
@pytest.mark.setting("slack.outbound_channels")
async def test_the_organizations_outbound_rules_govern_what_the_agent_may_send() -> None:
    model = ScriptedModel(
        script=[
            ("slack_send_message", {"channel": "C_NOT_LISTED", "text": "hi"}),
            ("slack_send_dm", {"user": "U_ANY", "text": "hi"}),
        ]
    )
    stack = Stack(
        model,
        _policy(
            outbound_channels=["C_ONLY_THIS"],
            dm_policy="none",
            allowed_tools=["slack_send_message", "slack_send_dm"],
        ),
    )

    await stack.send()

    posted = stack.posted()
    assert "Blocked:" in posted, "a channel outside the allowlist is refused"
    assert "disabled" in posted, "dm_policy none refuses direct messages"


@pytest.mark.setting("slack.auto_link_by_verified_email")
async def test_a_member_is_linked_by_verified_email_only_when_the_organization_enables_it() -> None:
    async def resolve(slack_user: str, email: str | None, org: str) -> tuple[str, str] | None:
        return ("member-1", org) if email == "alice@acme.test" else None

    async def identity(enabled: bool) -> tuple[str, str]:
        stack = Stack(ScriptedModel(), _policy(auto_link_by_verified_email=enabled))
        stack.assistant.resolve_user_fn = resolve  # type: ignore[assignment]
        stack.assistant._fetch_slack_user = AsyncMock(  # type: ignore[method-assign]
            return_value={"profile": {"email": "alice@acme.test"}}
        )
        return await stack.assistant._resolve_identity(
            "U1", "acme", _policy(auto_link_by_verified_email=enabled)
        )

    assert await identity(True) == ("member-1", "acme")
    assert await identity(False) == ("U1", "acme"), "without the setting the member stays anonymous"


@pytest.mark.setting("slack.web_base_url")
async def test_session_links_use_the_organizations_web_address() -> None:
    stack = Stack(ScriptedModel(), _policy(web_base_url="https://org.example/"))
    client = _live(stack)

    await stack.send()

    assert f"https://org.example/chat/{SESSION_ID}" in str(
        client.chat_postMessage.call_args_list[0]
    )


@pytest.mark.setting("slack.live_progress")
async def test_the_live_progress_message_exists_only_when_enabled() -> None:
    async def progress_posts(**settings: Any) -> int:
        stack = Stack(ScriptedModel(final_text="Done."), _policy(**settings))
        client = _live(stack)
        await stack.send()
        return sum("stop_turn" in str(call) for call in client.chat_postMessage.call_args_list)

    assert await progress_posts() == 1
    assert await progress_posts(live_progress=False) == 0


LONG_ANSWER = "Section.\n\n" + ("Details about the investigation. " * 400)


async def _uploads(**settings: Any) -> int:
    stack = Stack(ScriptedModel(final_text=LONG_ANSWER), _policy(**settings))
    client = _live(stack)
    await stack.send()
    return client.files_upload_v2.await_count


@pytest.mark.setting("slack.long_output_to_file")
async def test_long_answers_are_attached_as_a_file_only_when_enabled() -> None:
    assert await _uploads() == 1
    assert await _uploads(long_output_to_file=False) == 0


@pytest.mark.setting("slack.long_output_chars")
async def test_the_length_threshold_decides_when_an_answer_becomes_a_file() -> None:
    assert await _uploads(long_output_chars=100_000) == 0
    assert await _uploads(long_output_chars=200) == 1


def _callbacks() -> Any:
    from api.bootstrap.slack import _SlackCallbacks

    core = SimpleNamespace(db_manager=None, usage_ledger=None)
    return _SlackCallbacks(core)  # type: ignore[arg-type]


async def test_the_platform_hands_the_assistant_the_organizations_saved_slack_settings() -> None:
    from api.services.settings import save_org_config

    org = "wire_slack_plumbing"
    await save_org_config(
        None, org, "slack", {"dm_policy": "none", "web_base_url": "https://wired.example"}, []
    )

    policy = await _callbacks().resolve_policy(org)

    assert (policy.dm_policy, policy.web_base_url) == ("none", "https://wired.example")


@pytest.mark.setting("user_preferences.slack_notifications_enabled")
async def test_a_member_who_opted_out_gets_no_completion_notice() -> None:
    from api.services.settings import save_user_config

    async def notified(org: str, user: str) -> bool:
        stack = Stack(ScriptedModel())
        stack.assistant._notifications_enabled_fn = _callbacks().notifications_enabled
        stack.assistant._notify_turn_completion = AsyncMock()  # type: ignore[method-assign]
        stack.assistant.settings = stack.assistant.settings.model_copy(
            update={"slack_proactive_notify_after_seconds": 0}
        )
        await stack.assistant._maybe_notify(
            SimpleNamespace(text="finished"),
            "C1",
            "1.0",
            turn_started=0.0,
            org_id=org,
            member_id=user,
        )
        return stack.assistant._notify_turn_completion.await_count == 1  # type: ignore[attr-defined]

    assert await notified("wire_notify", "chatty") is True

    await save_user_config(
        None, "wire_notify", "quiet", "user_preferences", {"slack_notifications_enabled": False}
    )
    assert await notified("wire_notify", "quiet") is False
