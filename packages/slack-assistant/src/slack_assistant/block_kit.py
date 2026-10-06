"""Slack Block Kit formatting utilities for agent diffs and approval gates."""

from typing import Any

from specifications.interfaces.agent import FileDiff, InteractiveQuestion

SLACK_TEXT_BLOCK_LIMIT = 2800


def build_diff_blocks(
    diffs: list[FileDiff], max_chars: int = SLACK_TEXT_BLOCK_LIMIT
) -> list[dict[str, Any]]:
    """Format file diffs as syntax-highlighted diff blocks within Slack limits."""
    blocks: list[dict[str, Any]] = []

    for diff in diffs:
        status_indicator = "📝 Modified"
        if diff.is_new_file:
            status_indicator = "✨ Created"
        elif diff.deletions > 0 and diff.additions == 0:
            status_indicator = "🗑️ Deleted"

        header_text = f"*{status_indicator} `{diff.path}`* (+{diff.additions} / -{diff.deletions})"
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": header_text,
                },
            }
        )

        content = diff.diff_content
        if len(content) > max_chars:
            truncated_len = max_chars - 100
            content = f"{content[:truncated_len]}\n... [Diff truncated due to Slack size limits]"

        code_text = f"```diff\n{content}\n```"
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": code_text,
                },
            }
        )

    return blocks


def build_approval_blocks(
    action_id: str,
    title: str = "Human Approval Required",
    description: str | None = None,
) -> list[dict[str, Any]]:
    """Format decision gate with [Approve & Push] and [Reject] action buttons."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"⚠️ *{title}*\n{description or 'Review the changes before proceeding.'}",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Approve & Push",
                        "emoji": True,
                    },
                    "style": "primary",
                    "action_id": "approve_action",
                    "value": action_id,
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Reject",
                        "emoji": True,
                    },
                    "style": "danger",
                    "action_id": "reject_action",
                    "value": action_id,
                },
            ],
        },
    ]
    return blocks


def build_question_blocks(question: InteractiveQuestion) -> list[dict[str, Any]]:
    """Format interactive clarification questions as in-thread selectable options."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"❓ *{question.question_text}*",
            },
        }
    ]

    elements: list[dict[str, Any]] = []
    for opt in question.options:
        is_recommended = opt == question.default_recommended_option
        label = f"⭐ {opt}" if is_recommended else opt
        elements.append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": label[:75],
                    "emoji": True,
                },
                "action_id": "question_option_select",
                "value": f"{question.question_id}:{opt}",
            }
        )

    # Slack allows up to 5 elements per actions block; split if needed
    for i in range(0, len(elements), 5):
        blocks.append(
            {
                "type": "actions",
                "elements": elements[i : i + 5],
            }
        )

    return blocks
