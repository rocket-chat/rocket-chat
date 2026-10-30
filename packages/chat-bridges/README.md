# chat-bridges

Microsoft Teams and Mattermost for Rocket Chat, through one bridge (`ChatBridge`): a message
addressed to the bot becomes a turn of its thread's session, run by the platform's shared turn
pipeline, and the turn is rendered back into the thread (progress, questions, approvals, the
answer). Slack has its own integration (`slack-assistant`).

* `bridge.py`: the platform-neutral bridge. It never imports the API; the API passes
  `BridgeCallbacks` (`apps/api/src/api/bootstrap/chat.py`).
* `mattermost.py`: a bot account's WebSocket events in, REST replies out.
* `teams.py`: Bot Framework activities in (JWT checked against the Bot Framework's keys),
  Bot Connector replies out, only to Bot Framework hosts.

## Install

Part of the uv workspace: `uv sync --dev` at the repository root.

## Environment

| Variable | Meaning |
| :--- | :--- |
| `MATTERMOST_URL`, `MATTERMOST_BOT_TOKEN` | The Mattermost server and a bot account's access token. |
| `TEAMS_APP_ID`, `TEAMS_APP_PASSWORD` | The Azure Bot's app id and client secret. |
| `CHAT_BRIDGE_ORG_ID` | The organization the chat platforms work for. |

## Tests

`uv run pytest tests/unit/test_chat_bridges.py`
