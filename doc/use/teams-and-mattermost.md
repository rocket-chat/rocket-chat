---
title: Microsoft Teams and Mattermost
description: Work with the agent from a Teams or Mattermost thread.
---

# Microsoft Teams and Mattermost

When your operator has connected them, you can ask the agent from Microsoft Teams or Mattermost. Each thread is a [session](/use/sessions) in the web app, so you can follow and continue the work there.

## Asking

| Platform | You do |
| :--- | :--- |
| Teams | Mention the bot in a channel, or chat with it one to one. |
| Mattermost | Mention the bot (`@<bot>`), message it directly, or reply in a thread where it already works. |

The bot posts **Working on it…** and updates that message with the step it is on, then replaces it with the answer and a link to the session. A message sent while it works is queued and taken next.

**Questions and approvals.** When the agent asks a question, it lists the options: reply with the number of your choice. When something needs your approval, reply **approve** or **reject**. Only the person who asked can answer.

## Limits

Teams and Mattermost users are not linked to Rocket Chat accounts: their work runs as a guest member of the organization, without anyone's personal credentials. The agent cannot push or open pull requests from these platforms, and each person may start a few requests a minute. A failed turn says it failed without showing the raw error; the session in the web app has the details.

## For operators

See the [configuration reference](/install/configuration#microsoft-teams-and-mattermost). For Teams, create an Azure Bot, set its messaging endpoint to `https://<your address>/v1/chat/teams/messages` and add it to a Teams app. For Mattermost, create a bot account and give its token to the platform; the platform keeps one WebSocket connection to Mattermost (on one replica).
