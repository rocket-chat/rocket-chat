"""A bot's policy overrides merge into the organization's policy without losing any field."""

from __future__ import annotations

import dataclasses

import pytest
from slack_assistant.bot import SlackBot
from slack_assistant.policy import ChannelProfile, SlackOrgPolicy


def _bot(**policy: object) -> SlackBot:
    return SlackBot(id="b1", org_id="o", name="Bot", bot_token="x", app_token="y", policy=policy)


def _full_policy() -> SlackOrgPolicy:
    return SlackOrgPolicy(
        dm_policy="all",
        outbound_channels=frozenset({"C1"}),
        allowed_channel_types=frozenset({"im"}),
        auto_link_by_verified_email=True,
        allowed_tools=("file_read",),
        default_agent_persona="persona-x",
        web_base_url="https://rocket.example.com",
        live_progress=False,
        long_output_to_file=False,
        long_output_chars=900,
        restrict_linked_members=True,
        channel_profiles={"C9": ChannelProfile(agent_persona="p", allowed_tools=("a",))},
    )


def test_a_bot_without_overrides_keeps_every_field_of_the_organization_policy() -> None:
    org = _full_policy()
    assert _bot().apply_to(org) == org


@pytest.mark.setting("slack.restrict_linked_members")
def test_restrict_linked_members_survives_the_bot_merge() -> None:
    org = SlackOrgPolicy(restrict_linked_members=True)
    assert _bot().apply_to(org).restrict_linked_members is True
    assert _bot(restrict_linked_members=False).apply_to(org).restrict_linked_members is False


def test_every_policy_field_is_carried_through_the_merge() -> None:
    org = _full_policy()
    merged = _bot().apply_to(org)
    for item in dataclasses.fields(SlackOrgPolicy):
        assert getattr(merged, item.name) == getattr(org, item.name), item.name


def test_a_bot_override_wins_for_the_key_it_sets_only() -> None:
    org = _full_policy()
    merged = _bot(long_output_chars=1200).apply_to(org)
    assert merged.long_output_chars == 1200
    assert merged.restrict_linked_members is True
    assert merged.dm_policy == "all"
