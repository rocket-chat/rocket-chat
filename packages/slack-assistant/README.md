# `slack-assistant`

Native Slack Assistant Integration for Rocket Chat using Socket Mode.

## Overview

This package implements the Slack Assistant integration defined in `specifications/04_SLACK_ASSISTANT_INTEGRATION.md`. It establishes an outbound WebSocket connection via Slack's 100% Socket Mode (`AsyncSocketModeHandler`), interacts with the official Slack Assistant API, and streams real-time agent status updates and Block Kit interactive diffs.

### Key Features
1. **100% Socket Mode:** No public webhook ingress or static IP required. Connects via persistent outbound WebSocket (`xapp-...` + `xoxb-...`).
2. **Official Slack Assistant Helper (`AsyncAssistant`):**
   - Intercepts `assistant_thread_started` events, greets the user, and sets suggested prompt pills (`setSuggestedPrompts`).
   - Maps agent tool calls to real-time status spinners (`setStatus`).
3. **Interactive Block Kit:**
   - Syntax-highlighted code diff formatting with size limit guardrails.
   - Decision gates with interactive `[Approve & Push]` and `[Reject]` action buttons.
4. **Thread-to-Session 1:1 Mapping:**
   - Mapped 1:1 to Docker sandbox volumes (`sandbox_vol_slack_<channel>_<ts>`).
   - Automatic container hibernation for inactive threads while preserving volume state.

## Installation

```bash
uv sync
```

## Running Tests

```bash
# Unit tests
uv run pytest tests/unit/test_slack_assistant.py -v

# Full vertical interoperability test
uv run pytest tests/integration/test_slack_interoperability.py -v
```

## Environment Variables

- `SLACK_BOT_TOKEN`: Slack bot user token (`xoxb-...`).
- `SLACK_APP_TOKEN`: Slack app-level token with `connections:write` scope (`xapp-...`).
