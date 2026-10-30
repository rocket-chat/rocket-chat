# `slack-assistant`

Native Slack Assistant Integration for Rocket Chat using Socket Mode.

## Overview

This package implements the Slack Assistant integration for the Rocket Chat platform. It establishes an outbound WebSocket connection via Slack's 100% Socket Mode (`AsyncSocketModeHandler`), interacts with the official Slack Assistant API, and streams real-time agent status updates and Block Kit interactive diffs.

### Key Features
1. **100% Socket Mode:** No public webhook ingress or static IP required. Connects via persistent outbound WebSocket (`xapp-...` + `xoxb-...`).
2. **Official Slack Assistant Helper (`AsyncAssistant`):**
   - Intercepts `assistant_thread_started` events, greets the user, and sets suggested prompt pills (`setSuggestedPrompts`).
   - Maps agent tool calls to real-time status spinners (`setStatus`).
3. **Interactive Block Kit:**
   - Syntax-highlighted code diff formatting with size limit guardrails.
   - Decision gates with interactive `[Approve & Push]` and `[Reject]` action buttons.
4. **Every turn reaches its thread (`delivery.py`):** one watcher per Slack session renders each turn of that session in its thread, including turns no Slack message started (CI and review wakes, goal continuations, GitHub-started turns, restart resumes, messages sent from the web app). A message sent while a turn runs is queued durably and answered "Queued — I'll take this next.".
5. **Thread-to-Session 1:1 Mapping:**
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

- Bot tokens are not environment variables: the API builds a `SlackBot` (`bot.py`) per bot configured in the cockpit and passes it to `SlackAssistant(bot=...)`.
- `SLACK_BLOCKED_TOOLS`, `SLACK_USER_TURNS_PER_MINUTE`, `SLACK_CHANNEL_TURNS_PER_MINUTE`, `SLACK_MAX_ATTACHMENTS_PER_TURN` and the other `SLACK_*` limits in `config.py` apply to every bot.
