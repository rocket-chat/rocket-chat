"""Native Slack Assistant integration for Rocket Chat."""

from slack_assistant.assistant import SlackAssistant, clean_mention_text
from slack_assistant.block_kit import (
    build_approval_blocks,
    build_diff_blocks,
    build_question_blocks,
)
from slack_assistant.config import SlackAssistantSettings

__all__ = [
    "SlackAssistant",
    "SlackAssistantSettings",
    "build_approval_blocks",
    "build_diff_blocks",
    "build_question_blocks",
    "clean_mention_text",
]
