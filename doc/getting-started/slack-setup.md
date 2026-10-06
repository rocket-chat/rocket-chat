# Slack App Setup Guide

This guide walks you through creating and configuring a Slack App to connect with Rocket Chat using **Socket Mode** (100% outbound WebSocket connections, requiring zero public ingress or firewall ports).

---

## 1. Create a New Slack App

1. Navigate to the [Slack API Developer Portal](https://api.slack.com/apps).
2. Click **Create New App** > **From scratch**.
3. Set your **App Name** (e.g. `Rocket Chat`) and select your target workspace.
4. Click **Create App**.

---

## 2. Enable Socket Mode & Generate App-Level Token

Socket Mode allows the Rocket Chat backend to establish an encrypted, outbound WebSocket connection to Slack, eliminating the need for public webhooks or open ports:

1. In the left navigation bar, click **Socket Mode**.
2. Toggle **Enable Socket Mode** to `On`.
3. When prompted to generate an App-Level Token:
   - **Token Name:** `rocket-socket-token`
   - **Scope:** Ensure `connections:write` is selected.
4. Click **Generate**.
5. Copy the generated token starting with **`xapp-...`**. This is your `SLACK_APP_TOKEN`.

---

## 3. Configure Bot Token Scopes

Configure the permissions Rocket Chat requires to read thread context, post replies, and render decision cards:

1. In the left navigation, navigate to **OAuth & Permissions**.
2. Scroll down to **Scopes** > **Bot Token Scopes** and add the following scopes:

| Scope | Purpose |
| :--- | :--- |
| `app_mentions:read` | Allows the bot to respond when mentioned (`@Rocket`) in channels. |
| `chat:write` | Allows the bot to send messages, update spinners, and open threads. |
| `channels:history` | Allows reading conversation history in public channels to understand context. |
| `groups:history` | Allows reading conversation history in private channels where invited. |
| `im:history` | Allows the bot to read messages in 1-on-1 Direct Messages. |
| `im:write` | Allows the bot to reply in 1-on-1 Direct Messages. |

---

## 4. Subscribe to Bot Events

Configure the events Slack will push to the bot over the Socket Mode WebSocket:

1. In the left navigation, navigate to **Event Subscriptions**.
2. Toggle **Enable Events** to `On`.
3. Under **Subscribe to bot events**, click **Add Bot User Event** and add:
   - `app_mention` (triggers the agent when mentioned in any channel).
   - `message.im` (triggers the agent in 1-on-1 direct messages).
4. Click **Save Changes** at the bottom of the page.

---

## 5. Enable Interactivity (Decision Gates)

To allow the agent to prompt engineers with interactive buttons and select menus (via `ask_question`):

1. In the left navigation, navigate to **Interactivity & Shortcuts**.
2. Toggle **Interactivity** to `On`.
3. *(Note: Because Socket Mode is enabled, no Request URL is required! Slack will send button clicks directly over the existing WebSocket connection).*
4. Click **Save Changes**.

---

## 6. Install App to Workspace & Obtain Bot Token

1. In the left navigation, click **Install App**.
2. Click **Install to Workspace** and authorize the permissions.
3. Copy the **Bot User OAuth Token** starting with **`xoxb-...`**. This is your `SLACK_BOT_TOKEN`.

---

## 7. Rocket Chat Configuration

Provide both tokens in your `.env` or deployment configuration:

```bash
SLACK_BOT_TOKEN="xoxb-your-bot-user-token"
SLACK_APP_TOKEN="xapp-your-app-level-token"
```

Restart Rocket Chat. The logs will confirm the connection:
```text
[INFO] Initializing embedded Slack Socket Mode Assistant...
[INFO] Slack Socket Mode client connected to Slack API successfully.
```

Invite `@Rocket` to any channel (`/invite @Rocket`) and submit an instruction:
```text
@Rocket check if all unit tests are passing in this repo.
```

---

## 8. Slack User Account Linking (`/rocket link`)

To allow the Slack Assistant to operate securely under a developer's identity, users can link their Slack identity to their Rocket Chat account:

1. **Magic Link Linking**:
   - In any Slack channel or DM with `@Rocket`, type:
     ```text
     /rocket link
     ```
   - The bot replies with an ephemeral, cryptographically signed magic link valid for 15 minutes.
   - Clicking the link opens Rocket Chat and pairs the Slack User ID (`U...`) with the Rocket Chat User ID.

2. **Automated Verified Email Matching**:
   - If the user's verified Slack email matches their corporate SSO/OIDC email in Rocket Chat, the system can automatically link contexts without manual intervention (configurable by organization policy).

---

## 9. Execution Context & Session Management

Engineers can control how the Slack Assistant executes instructions and interacts with sandbox workspaces:

### A. Context Selection: Bot vs Personal User
- **Bot Context**: The agent runs with standard organization-level permissions and default tool scopes.
- **Personal User Context**: The agent runs under the linked developer's credentials, inheriting their GitHub OAuth token, personal SSH keys, and individual sandbox workspace limits.

### B. Interactive Session Switching (`/rocket session`)
- Each Slack thread is automatically mapped to a distinct Rocket Chat mission session.
- Typing `/rocket session` opens a Slack Block Kit modal allowing engineers to:
  1. Inspect the linked session's current status and active sandbox workspace.
  2. Switch the active thread context to an existing ongoing web mission session.
  3. Fork the current thread into a brand new isolated sandbox session.

---

## 10. Slack Settings Suite (`/settings/slack`)

Administrators and developers can configure Slack behavior via the web UI:

* **Tool Whitelist**: Fine-grained checkboxes restricting which tools the Slack Assistant can invoke (e.g. read-only file access vs full bash execution).
* **Execution Context Mode**: Default execution context (`bot_service_account` vs `linked_user`).
* **Thread Auto-Archive**: Automatic timeout period for hibernating inactive Slack thread sandboxes to conserve cluster resources.
* **Notification Preferences**: Configure whether tool decision gates (`ask_question`) prompt directly in the Slack thread with interactive buttons.
