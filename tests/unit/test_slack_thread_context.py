"""What the Slack thread transcripts show of messages posted with blocks or attachments."""

from __future__ import annotations

import pytest
from slack_assistant.thread_context import _to_messages, message_text, render_transcript


def _mrkdwn(text: str) -> dict[str, str]:
    return {"type": "mrkdwn", "text": text}


# A run report as a CI bot posts it: the text is only the notification fallback.
REPORT = {
    "ts": "1792600000.000100",
    "bot_id": "B0B9RDC490V",
    "text": "Detail of it_dbc6q54a9kfs73f5fgs0",
    "blocks": [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "❌ Integration qa · 11/12 flows"},
        },
        {
            "type": "section",
            "text": _mrkdwn(
                "❌ *payment/start-failure*\n>*Failed at* start answers the declined card"
            ),
        },
        {"type": "section", "fields": [_mrkdwn("*Env* qa"), _mrkdwn("*Suite* full")]},
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                _mrkdwn("🧹 *Cleanup*"),
                {"type": "image", "image_url": "https://x/y.png", "alt_text": "logo"},
                _mrkdwn("*Documents* 42"),
            ],
        },
        {
            "type": "actions",
            "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Rerun"}}],
        },
    ],
}


def test_a_message_with_blocks_shows_what_its_blocks_show() -> None:
    assert message_text(REPORT) == "\n".join(
        [
            "*❌ Integration qa · 11/12 flows*",
            "❌ *payment/start-failure*\n>*Failed at* start answers the declined card",
            "*Env* qa",
            "*Suite* full",
            "---",
            "🧹 *Cleanup* · *Documents* 42",
        ]
    )


def test_a_message_typed_by_a_person_keeps_its_text() -> None:
    typed = {
        "text": "<@U0BST1SE4UW> peux-tu regarder ?",
        "blocks": [
            {"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": []}]}
        ],
    }
    assert message_text(typed) == "<@U0BST1SE4UW> peux-tu regarder ?"


def test_attachments_follow_the_text() -> None:
    alert = {
        "text": "[FIRING:1] HighErrorRate",
        "attachments": [
            {
                "title": "HighErrorRate",
                "text": "5xx above 2% on gateway-partner",
                "fields": [{"title": "env", "value": "beta"}],
                "fallback": "ignored when the attachment shows something",
            },
            {"fallback": "only a fallback"},
        ],
    }
    assert message_text(alert) == "\n".join(
        [
            "[FIRING:1] HighErrorRate",
            "HighErrorRate",
            "5xx above 2% on gateway-partner",
            "env: beta",
            "only a fallback",
        ]
    )


def test_a_message_without_anything_readable_is_empty() -> None:
    assert message_text({"blocks": [{"type": "actions", "elements": []}]}) == ""
    assert message_text({}) == ""


@pytest.mark.asyncio
async def test_the_transcript_shows_the_blocks_of_a_bot_report() -> None:
    mention = {
        "ts": "1792600000.000200",
        "bot_id": "B0B9RDC490V",
        "text": "<@U0BST1SE4UW> please diagnose",
    }

    async def names(user: str) -> str:
        return {"B0B9RDC490V": "goclaude"}.get(user, user)

    transcript = await render_transcript(_to_messages([REPORT, mention]), names)
    assert (
        "- goclaude (bot): *❌ Integration qa · 11/12 flows*\n❌ *payment/start-failure*"
        in transcript
    )
    assert "Detail of it_dbc6q54a9kfs73f5fgs0" not in transcript
    assert transcript.endswith("- goclaude (bot): <@U0BST1SE4UW> please diagnose")


@pytest.mark.asyncio
async def test_one_huge_report_stays_within_the_transcript_budget() -> None:
    report = {
        "ts": "1.0",
        "bot_id": "B1",
        "text": "fallback",
        "blocks": [{"type": "section", "text": _mrkdwn("x" * 3000)} for _ in range(50)],
    }

    async def names(user: str) -> str:
        return user

    transcript = await render_transcript(_to_messages([report]), names, max_chars=24000)
    assert len(transcript) <= 24000
    assert transcript.endswith("(message truncated)")
