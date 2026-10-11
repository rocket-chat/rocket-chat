"""Slack mrkdwn formatting, especially mention handling.

The model frequently writes a bare ``@U123`` (or ``@here``); Slack only linkifies the
bracketed forms, so the raw text leaked into messages. The formatter wraps bare mentions
while leaving already-bracketed ones and code spans untouched.
"""

from __future__ import annotations

from slack_assistant.block_kit import markdown_to_slack_mrkdwn


def test_bare_user_mention_is_wrapped() -> None:
    assert markdown_to_slack_mrkdwn("ping @U02D6G6GCMQ please") == "ping <@U02D6G6GCMQ> please"


def test_already_bracketed_mention_is_not_double_wrapped() -> None:
    assert markdown_to_slack_mrkdwn("ping <@U02D6G6GCMQ>") == "ping <@U02D6G6GCMQ>"


def test_legacy_w_user_id_is_wrapped() -> None:
    assert markdown_to_slack_mrkdwn("@W0123456789 hi") == "<@W0123456789> hi"


def test_special_mentions_are_wrapped() -> None:
    assert markdown_to_slack_mrkdwn("@here standup") == "<!here> standup"
    assert markdown_to_slack_mrkdwn("@channel please review") == "<!channel> please review"


def test_mentions_inside_code_are_left_alone() -> None:
    assert markdown_to_slack_mrkdwn("`@U02D6G6GCMQ`") == "`@U02D6G6GCMQ`"


def test_email_like_text_is_not_treated_as_a_mention() -> None:
    # A word character before the '@' means it is not a Slack mention.
    assert markdown_to_slack_mrkdwn("write to bob@U02D6G6GCMQ") == "write to bob@U02D6G6GCMQ"
